"""Fuente SIOMAA: descubrimiento + descarga (email de verificación) + parseo del PDF.

El "Informe de Mercado 4W" de SIOMAA es un PDF gratuito pero detrás de un flujo de
verificación por email. La tienda sólo expone el ÚLTIMO informe de forma directa, así que
el ETL incremental es "bajar el último" (no se puede pedir un mes arbitrario por URL como
ADEFA). El histórico se carga aparte desde los PDFs ya bajados (ver load_history.py).

Flujo de descarga (la tienda manda un token de 6 dígitos por mail):
  1. GET  /eShop/Contact/{id}?isFree=true    (carga el form)
  2. POST /eShop/Contact/{id}                 (datos de contacto -> dispara el email)
  3. se recibe el token en un email temporario (mail.tm)
  4. POST /eShop/ContactVerification         (envía el token -> devuelve el ZIP con el PDF)

Parseo (pdfplumber): la Tabla 1 ("Resumen del mercado") tiene el texto posicionado por
coordenadas y sale entreverado con extract_text(). Se reconstruye por posición: se agrupan
los chars por fila (y) y se cortan en celdas por gaps de x. De cada fila de categoría se
toma la 1ª cifra = unidades del mes. El mes/año se detectan del propio encabezado del PDF.
"""
from __future__ import annotations

import io
import random
import re
import string
import time
import zipfile
from collections import defaultdict

import requests

from etl.core import meses
from . import config

BASE = "https://www.siomaa.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 30
REPORT_NAME = "Informe de Patentamientos Mercado 4W"

# Nombre de mes completo + año de 4 dígitos (título del informe, nombre del archivo).
_MES_RX = re.compile(meses.alternancia(formas="largas") + r"\s*\.?\s*(\d{4})", re.IGNORECASE)

# Mes abreviado (como en el encabezado de columna de la Tabla 1, p.ej. 'Ene.2022' en los
# LITE o 'JUN.26' en los full). El año puede venir en 4 dígitos (LITE) o 2 (full). Sin \b
# final: en los full las columnas salen pegadas ('JUN.26MAY.26...').
#
# Las variantes salen de `etl.core.meses`: este regex se armaba con un mapa propio que tenía
# `sep` y `set` pero NO `sept`, así que un encabezado "Sept.2026" no matcheaba y el mes se
# perdía en silencio. El helper además ordena las ramas de más larga a más corta, que es lo que
# evita que `sep` le gane a `sept` y rompa el match.
_ABBR_RX = re.compile(meses.alternancia(formas="cortas") + r"\.?\s*(\d{4}|\d{2})",
                      re.IGNORECASE)


# --------------------------------------------------------------------------- #
# mail.tm — email temporario para recibir el token de verificación
# --------------------------------------------------------------------------- #
class MailTmClient:
    API = "https://api.mail.tm"

    def __init__(self):
        self.token = None
        self.email = None

    def create_account(self, retries=3):
        for attempt in range(retries):
            try:
                domains = requests.get(f"{self.API}/domains", timeout=15).json()["hydra:member"]
                domain = domains[0]["domain"]
                user = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
                self.email = f"{user}@{domain}"
                pw = "".join(random.choices(string.ascii_letters + string.digits, k=16))
                requests.post(f"{self.API}/accounts",
                              json={"address": self.email, "password": pw}, timeout=15)
                tok = requests.post(f"{self.API}/token",
                                    json={"address": self.email, "password": pw}, timeout=15)
                self.token = tok.json()["token"]
                return self.email
            except Exception as e:
                print(f"  mail.tm intento {attempt + 1}/{retries} falló: {e}")
                time.sleep(2)
        raise RuntimeError("no se pudo crear la cuenta mail.tm")

    def messages(self):
        try:
            h = {"Authorization": f"Bearer {self.token}"}
            r = requests.get(f"{self.API}/messages", headers=h, timeout=15)
            return r.json()["hydra:member"] if r.status_code == 200 else []
        except Exception:
            return []

    def message(self, msg_id):
        try:
            h = {"Authorization": f"Bearer {self.token}"}
            r = requests.get(f"{self.API}/messages/{msg_id}", headers=h, timeout=15)
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}


def _period_from_name(name: str) -> tuple[int | None, int | None]:
    """(año, mes) del displayName del informe (p.ej. '... - Junio 2026'). None si no matchea."""
    m = _MES_RX.search(name)
    if m:
        return int(m.group(2)), meses.numero(m.group(1))
    return None, None


# --------------------------------------------------------------------------- #
# Descubrimiento y descarga
# --------------------------------------------------------------------------- #
def get_latest_report() -> dict | None:
    """El último informe 4W gratuito de la tienda: {id, name, year, month} o None."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                      "Referer": f"{BASE}/eShop"})
    payload = {"sEcho": "1", "iColumns": "1", "iDisplayStart": "0", "iDisplayLength": "100",
               "mDataProp_0": "0", "iSortCol_0": "0", "sSortDir_0": "desc",
               "iSortingCols": "1", "bSortable_0": "true"}
    r = s.post(f"{BASE}/eShop/AjaxListRequest", data=payload, timeout=TIMEOUT)
    reports = [p for p in r.json().get("aaData", [])
               if REPORT_NAME in p.get("displayName", "") and p.get("isFree") == "TRUE"]
    if not reports:
        return None
    reports.sort(key=lambda x: int(x["id"]), reverse=True)  # el más nuevo = id más alto
    top = reports[0]
    year, month = _period_from_name(top["displayName"])
    return {"id": int(top["id"]), "name": top["displayName"], "year": year, "month": month}


def download_pdf_bytes(product_id: int) -> bytes:
    """Corre el flujo de verificación por email y devuelve los bytes del PDF (extraído del ZIP)."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    mail = MailTmClient()
    email = mail.create_account()
    time.sleep(2)

    contact = f"{BASE}/eShop/Contact/{product_id}"
    s.get(f"{contact}?isFree=true", timeout=TIMEOUT)
    time.sleep(2)
    form = {"Email": email, "Company": "Research", "FirstName": "Analista",
            "LastName": "Datos", "IdentifyType": "1", "IdentifyValue": "12345678",
            "JobPosition": "Analyst", "PhoneNumber": "+541112345678",
            "IdProduct": str(product_id), "IsFree": "true",
            "IdProductConcept": str(product_id)}
    resp = s.post(contact, data=form, allow_redirects=True, timeout=TIMEOUT)
    m = re.search(r"idContact=(\d+)", resp.url)
    id_contact = m.group(1) if m else ""

    for _ in range(60):  # esperar el email con el token (hasta ~4 min)
        msgs = mail.messages()
        if msgs:
            break
        time.sleep(4)
    else:
        raise RuntimeError("no llegó el email de verificación de SIOMAA")

    body = mail.message(msgs[0]["id"])
    token = None
    m = re.search(r"\b\d{6}\b", body.get("text", body.get("html", "")))
    if m:
        token = m.group(0)
    if not token:
        raise RuntimeError("no se pudo extraer el token de 6 dígitos del email")

    verify = {"IsFree": "True", "IdProductConcept": "", "IdProduct": str(product_id),
              "IdContact": id_contact, "Email": email, "Token": token}
    r = s.post(f"{BASE}/eShop/ContactVerification", data=verify,
               allow_redirects=True, timeout=TIMEOUT)
    ctype = r.headers.get("Content-Type", "").lower()
    if not any(c in ctype for c in ("zip", "octet-stream", "pdf")):
        raise RuntimeError(f"respuesta inesperada al descargar (Content-Type: {ctype})")
    return _pdf_from_zip(r.content)


def _pdf_from_zip(blob: bytes) -> bytes:
    """Extrae el primer PDF de un ZIP en memoria (o devuelve el blob si ya es un PDF)."""
    if blob[:4] == b"%PDF":
        return blob
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".pdf"))
        return z.read(name)


# --------------------------------------------------------------------------- #
# Parseo de la Tabla 1 (por coordenadas)
# --------------------------------------------------------------------------- #
def _row_cells(chars, gap: float = 3.0) -> list[str]:
    """Reconstruye las celdas de una fila: junta chars por x, corta cuando el gap supera `gap`."""
    chars = sorted(chars, key=lambda c: c["x0"])
    cells, cur, prev_x1 = [], "", None
    for c in chars:
        if prev_x1 is not None and c["x0"] - prev_x1 > gap:
            cells.append(cur)
            cur = ""
        cur += c["text"]
        prev_x1 = c["x1"]
    if cur:
        cells.append(cur)
    return cells


def _rows_by_y(page, ytol: float = 2.0) -> list[list]:
    """Agrupa los chars de la página en filas por su coordenada vertical."""
    buckets = defaultdict(list)
    for c in page.chars:
        buckets[round(c["top"] / ytol)].append(c)
    return [buckets[k] for k in sorted(buckets)]


def _detect_period(rows: list[list]) -> tuple[int | None, int | None]:
    """(año, mes) del encabezado de la Tabla 1, reconstruido por coordenadas.

    Fuente principal: el encabezado de columna del mes corriente (el primero), p.ej.
    'Ene.2022' (LITE) o 'JUN.26' (full). Fallback: el nombre de mes completo del título.
    """
    for chars in rows[:8]:
        line = "".join(c["text"] for c in sorted(chars, key=lambda c: c["x0"]))
        m = _ABBR_RX.search(line)
        if m:
            year = int(m.group(2))
            return meses.anio_2d(year), meses.numero(m.group(1))
        m = _MES_RX.search(line)
        if m:
            return int(m.group(2)), meses.numero(m.group(1))
    return None, None


def parse_report(pdf_bytes: bytes) -> tuple[int | None, int | None, dict]:
    """(año, mes, {serie: unidades}) desde la Tabla 1 del PDF. dict vacío si no se encontró."""
    import pdfplumber  # import perezoso: solo patentamientos lo necesita

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            rows = _rows_by_y(page)
            flat = "".join("".join(c["text"] for c in sorted(r, key=lambda c: c["x0"]))
                           for r in rows)
            norm = re.sub(r"[\s.]", "", flat).upper()
            if "TABLA1" not in norm or "TOTALMERCADO" not in norm:
                continue
            year, month = _detect_period(rows)
            out: dict[str, float] = {}
            for chars in rows:
                cells = _row_cells(chars)
                line = "".join(cells).replace(" ", "")
                for label, serie in config.LABEL_TO_SERIE.items():
                    if serie in out or not line.startswith(label.replace(" ", "")):
                        continue
                    nums = [c for c in cells if re.fullmatch(r"[\d.]+", c)]
                    if nums:
                        out[serie] = float(int(nums[0].replace(".", "")))
                    break
            return year, month, out
    return None, None, {}
