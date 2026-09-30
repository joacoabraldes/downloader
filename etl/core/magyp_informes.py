"""Capa compartida de los Informes de Estimaciones Agrícolas de MAGyP (semanal y mensual).

Dos datasets (`estimaciones_semanal`, `estimaciones_mensual`) salen del MISMO índice y con la
misma mecánica, así que lo común vive acá y cada `source.py` sólo parsea SU PDF:

  - resolver los PDFs de un mes desde el índice mensual,
  - bajar con presupuesto de requests y pausa (host MAGyP compartido),
  - registrar qué PDFs ya se procesaron (por URL),
  - cargar las filas append-only comparando en memoria.

Fuente
------
    https://www.magyp.gob.ar/sitio/areas/estimaciones/estimaciones/informes/?mes=AAAA-MM

Una página por mes con los links a los PDFs de ese mes (semanales y el mensual). Los nombres NO
son predecibles ("Informe Semanal" vs "Informe semanal", formatos distintos en 2012), así que se
scrapea el índice en vez de armar la URL. Semanal = jueves (miércoles si es feriado); mensual =
un jueves a mitad de mes, el mismo día que un semanal. Sólo PDF, sin planillas.

Por qué hay presupuesto de requests
-----------------------------------
www.magyp.gob.ar lo usan otros 6 ETLs desde esta misma IP (compras_granos, granos, bovinos,
estimaciones_agricolas, ...). Un backfill agresivo no rompe un ETL, rompe todos: en el backfill
de compras_granos el sitio empezó a devolver 403 en TODO tras ~700 páginas seguidas. Por eso:

  - pausa >= PAUSA segundos antes de CADA request (índice o PDF),
  - reintentos bajos (REINTENTOS),
  - `max_requests`: la corrida se corta limpia al llegar al tope y la siguiente sigue donde quedó
    (los PDFs procesados quedan registrados). El backfill se hace en tandas.

Corrida normal: 1 GET del índice del mes actual (+1 del mes anterior los primeros días del mes,
por si el informe del último jueves se subió tarde) y sólo los PDFs que no estén registrados.

Cache en disco (`--cache DIR`, opcional, pensado para el backfill)
-----------------------------------------------------------------
Guarda los PDFs y los índices de meses CERRADOS (terminados hace más de DIAS_INDICE_CERRADO
días). Sirve para que el segundo dataset no vuelva a pedir los mismos índices y para re-parsear
sin volver a bajar si hay que corregir el parser. El cron no lo usa.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin

from etl.core import db, http, report

BASE = "https://www.magyp.gob.ar"
URL_INDICE = BASE + "/sitio/areas/estimaciones/estimaciones/informes/?mes={mes}"
PAUSA = 5.0            # segundos antes de cada request (host compartido: >= 4 s)
REINTENTOS = 2         # un reintento como mucho (espera 5 s): nunca un loop contra el host
TIMEOUT_INDICE = 60
TIMEOUT_PDF = 180
DIAS_INDICE_CERRADO = 10  # un índice de un mes terminado hace más que esto se puede cachear
ESTADO = "publicado"

# href del PDF dentro del índice. El texto del link repite el nombre ("Informe semanal al ...").
_RE_LINK = re.compile(r'href="(/sitio/areas/estimaciones/_archivos/estimaciones/[^"]+?\.pdf)"',
                      re.I)
_RE_FECHA_NOMBRE = re.compile(r"al\s+(\d{2})_(\d{2})_(\d{4})", re.I)
_RE_FECHA_PREFIJO = re.compile(r"/(\d{2})(\d{2})(\d{2})_[^/]*$")


@dataclass(frozen=True)
class Informe:
    url: str            # absoluta, con los espacios codificados (%20)
    tipo: str           # 'semanal' | 'mensual'
    fecha: dt.date      # fecha del informe (la del nombre del archivo)
    nombre: str         # nombre del archivo, para los logs


def normalizar(s: str) -> str:
    """minúsculas, sin tildes ni diéresis, espacios colapsados."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def slug(s: str) -> str:
    """'Pigüé' -> 'pigue', 'Pcia. R. S. Peña' -> 'pcia_r_s_pena'."""
    return re.sub(r"[^a-z0-9]+", "_", normalizar(s)).strip("_")


def parse_indice(html: str) -> list[Informe]:
    """Informes semanales y mensuales linkeados en una página del índice (sin duplicados)."""
    vistos: dict[str, Informe] = {}
    for href in _RE_LINK.findall(html):
        nombre = href.rsplit("/", 1)[-1]
        n = normalizar(nombre)
        if "informe" not in n:
            continue
        if "semanal" in n:
            tipo = "semanal"
        elif "mensual" in n:
            tipo = "mensual"
        else:
            continue
        m = _RE_FECHA_NOMBRE.search(nombre)
        if m:
            d, mth, y = (int(g) for g in m.groups())
        else:
            m = _RE_FECHA_PREFIJO.search(href)
            if not m:
                continue  # sin fecha reconocible: no se adivina
            y, mth, d = 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))
        url = urljoin(BASE, quote(href, safe="/%"))
        vistos[url] = Informe(url=url, tipo=tipo, fecha=dt.date(y, mth, d), nombre=nombre)
    return sorted(vistos.values(), key=lambda i: (i.fecha, i.url))


def meses(desde: str | None, hasta: str | None, hoy: dt.date) -> list[str]:
    """Meses 'AAAA-MM' a revisar. Sin rango: el actual, y el anterior los primeros 7 días."""
    if desde is None:
        actual = hoy.replace(day=1)
        out = [actual]
        if hoy.day <= 7:
            out.insert(0, (actual - dt.timedelta(days=1)).replace(day=1))
        return [f"{m:%Y-%m}" for m in out]
    ini = dt.date.fromisoformat(desde + "-01")
    fin = dt.date.fromisoformat((hasta or f"{hoy:%Y-%m}") + "-01")
    out = []
    while ini <= fin:
        out.append(f"{ini:%Y-%m}")
        ini = (ini + dt.timedelta(days=32)).replace(day=1)
    return out


# Desde qué mes un índice sin informes del tipo es anómalo. Antes la serie es irregular (el
# índice de 2005-05 no trae ninguno; 2008-05 trae semanales pero no mensual): un backfill por
# esos años no debe fallar por huecos conocidos. Lo cargado va de 2025-05 en adelante.
MES_MIN_CONTROL = "2012-01"
DIA_SEMANAL_ESPERADO = 10  # pasado este día del mes actual ya tuvo que salir algún semanal


def sin_informes_es_falla(mes: str, tipo: str, hoy: dt.date) -> bool:
    """¿Un índice con 0 informes de `tipo` es falla (índice cambiado / parser roto)?

    Semanas de feriado pueden quedar sin informe, pero un mes entero sin ninguno no. Un mes
    ya terminado sin informes -> falla (los dos tipos). El mes en curso: el semanal falla recién
    pasado el día DIA_SEMANAL_ESPERADO; el mensual sale a mitad de mes, nunca falla en curso.
    Meses futuros (--hasta adelante) y anteriores a MES_MIN_CONTROL no fallan.
    """
    if mes < MES_MIN_CONTROL:
        return False
    actual = f"{hoy:%Y-%m}"
    if mes < actual:
        return True
    if mes == actual:
        return tipo == "semanal" and hoy.day > DIA_SEMANAL_ESPERADO
    return False


class SinPresupuesto(Exception):
    """Se llegó al tope de requests de esta corrida: se corta limpio, no es una falla."""


class SinRed(Exception):
    """--sin-red y el recurso no está en la cache: no se hace el request."""


class Cliente:
    """HTTP contra MAGyP con pausa antes de cada request, tope por corrida y cache opcional."""

    def __init__(self, max_requests: int | None, cache: Path | None, hoy: dt.date,
                 sin_red: bool = False):
        self.max_requests = max_requests
        self.sin_red = sin_red
        self.cache = cache
        self.hoy = hoy
        self.requests = 0
        if cache:
            cache.mkdir(parents=True, exist_ok=True)
        import urllib3
        urllib3.disable_warnings()  # verify=False: el cert de magyp.gob.ar no valida en el server

    def _get(self, url: str, timeout: int):
        if self.sin_red:
            raise SinRed(f"--sin-red: {url} no está en la cache")
        if self.max_requests is not None and self.requests >= self.max_requests:
            raise SinPresupuesto(f"tope de {self.max_requests} requests alcanzado")
        time.sleep(PAUSA)
        self.requests += 1
        print(f"  GET {url}", file=sys.stderr)
        return http.fetch(url, timeout=timeout, reintentos=REINTENTOS, verify=False)  # cert de magyp

    def _cache_path(self, clave: str, ext: str) -> Path | None:
        if not self.cache:
            return None
        return self.cache / (hashlib.sha1(clave.encode()).hexdigest()[:16] + ext)

    def indice(self, mes: str) -> str:
        """HTML del índice de un mes. Cachea sólo meses cerrados (su índice ya no cambia)."""
        url = URL_INDICE.format(mes=mes)
        fin_mes = (dt.date.fromisoformat(mes + "-01") + dt.timedelta(days=32)).replace(day=1)
        cerrado = (self.hoy - fin_mes).days > DIAS_INDICE_CERRADO
        path = self._cache_path(url, ".html") if cerrado else None
        if path and path.is_file():
            return path.read_text(encoding="utf-8")
        resp = self._get(url, TIMEOUT_INDICE)
        resp.encoding = resp.apparent_encoding
        html = resp.text
        if "estimaciones" not in html.lower():
            raise ValueError(f"índice {mes}: la respuesta no parece la página de informes")
        if path:
            path.write_text(html, encoding="utf-8")
        return html

    def pdf(self, url: str) -> bytes:
        path = self._cache_path(url, ".pdf")
        if path and path.is_file():
            return path.read_bytes()
        raw = self._get(url, TIMEOUT_PDF).content
        if not raw.startswith(b"%PDF"):
            raise ValueError("la respuesta no es un PDF")
        if path:
            path.write_bytes(raw)
        return raw


# --- registro de informes procesados ------------------------------------------------------

def procesados(conn, tabla: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(f"select url from {tabla}")
        return {r[0] for r in cur}


def ultima_fecha(conn, tabla: str) -> dt.date | None:
    with conn.cursor() as cur:
        cur.execute(f"select max(date_informe) from {tabla}")
        r = cur.fetchone()
    return r[0] if r else None


def registrar(conn, tabla: str, inf: Informe, filas: int, nuevos: int, actualizados: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"insert into {tabla} (url, date_informe, filas, nuevos, actualizados) "
            # Reproceso (--force): nuevos/actualizados se ACUMULAN (filas escritas desde esa URL
            # en total), no se pisan con los ceros de una pasada sin cambios.
            f"values (%s,%s,%s,%s,%s) on conflict (url) do update set "
            f"procesado_at = now(), filas = excluded.filas, "
            f"nuevos = {tabla}.nuevos + excluded.nuevos, "
            f"actualizados = {tabla}.actualizados + excluded.actualizados",
            (inf.url, inf.fecha, filas, nuevos, actualizados),
        )
    conn.commit()


# --- carga append-only ---------------------------------------------------------------------

def cargar(conn, *, table: str, key_cols: list[str], filas: list[dict], date_informe: dt.date,
           fuente: str, extra_cols: list[str], rep: report.Report) -> tuple[int, int]:
    """Carga las filas de UN informe. Devuelve (nuevos, actualizados).

    Mismo esquema que estimaciones_agricolas: se lee una vez el último snapshot de las claves de
    las campañas del informe, se compara en memoria (`db._changed`, tolerancia 1e-6), las claves
    nuevas van por `bulk_insert` y las que cambiaron por `insert_if_changed` (que vuelve a
    comparar contra la base). Con ~35 ms de ida y vuelta, `insert_if_changed` fila por fila
    sobre ~3.000 filas por informe serían minutos por PDF.

    `date_informe` NO es parte de la clave: es la fecha del informe en el que apareció ESE
    valor (columna de contexto, como `fecha_actualizacion` en estimaciones_agricolas). Un valor
    que se repite en el informe siguiente no genera fila; uno revisado sí.

    Se compara contra el snapshot VIGENTE A LA FECHA del informe (date_informe <= la suya), no
    contra el último: así un informe viejo cargado tarde (un PDF que no parseaba y se arregló)
    deja la historia bien. Contra el último, un valor igual al de un informe posterior no se
    escribía y "lo que decía el informe X" devolvía el valor de antes de X. Tras cargar uno
    viejo hay que reprocesar (`--force`, en orden) los posteriores: donde el valor intercalado
    no coincide con lo que ellos decían, vuelven a escribir el suyo y `_actual` queda bien.
    Por eso tampoco se usa `db.insert_if_changed` para las revisadas: compara contra el último
    por `ingested_at`, que tras una carga fuera de orden es el informe viejo, y descartaría en
    silencio filas necesarias. La comparación de acá es la misma (`db._changed`, 1e-6) y
    la carga sigue append-only. En una corrida en orden (el cron) da lo mismo que antes.
    """
    vals = ["valor"]
    campanias = sorted({f["campania"] for f in filas})
    sql = (f"select distinct on ({', '.join(key_cols)}) {', '.join(key_cols)}, valor "
           f"from {table} where estado = %s and campania = any(%s) and date_informe <= %s "
           f"order by {', '.join(key_cols)}, date_informe desc, ingested_at desc")
    previos: dict[tuple, dict] = {}
    with conn.cursor() as cur:
        cur.execute(sql, (ESTADO, campanias, date_informe))
        for r in cur:
            previos[tuple(r[:-1])] = {"valor": float(r[-1]) if r[-1] is not None else None}

    cols = key_cols + vals + extra_cols + ["date_informe", "estado", "fuente"]
    lote: list[tuple] = []
    actualizados = 0
    # Claves de `previos` ausentes en este informe: NO se reportan (a diferencia de
    # estimaciones_agricolas / bcba_pas). Cada informe trae sólo los cultivos activos de la
    # semana/mes, así que una clave que no aparece es lo normal, no una señal.
    for f in filas:
        if f["valor"] is None:
            # Como insert_if_changed: un vacío no pisa un valor previo ni abre una clave nueva
            # con NULL (antes, en una clave sin snapshot, se escribía la fila con valor NULL).
            rep.tally("saltado")
            continue
        clave = tuple(f[c] for c in key_cols)
        extra = {c: f.get(c) for c in extra_cols}
        extra["date_informe"] = date_informe
        prev = previos.get(clave)
        if prev is None:
            lote.append(clave + (f["valor"],) + tuple(extra.values()) + (ESTADO, fuente))
            continue
        if not db._changed(prev, f, vals, 1e-6):
            rep.tally("sin_cambios")
            continue
        lote.append(clave + (f["valor"],) + tuple(extra.values()) + (ESTADO, fuente))
        actualizados += 1
    escritas = db.bulk_insert(conn, table=table, cols=cols, rows=lote)
    nuevos = escritas - actualizados
    for _ in range(nuevos):
        rep.tally("nuevo")
    for _ in range(actualizados):
        rep.tally("actualizado")
    return nuevos, actualizados


# --- driver común de las dos corridas ------------------------------------------------------

def correr(*, dataset: str, tipo: str, tabla: str, tabla_informes: str, key_cols: list[str],
           extra_cols: list[str], parse, argv, descripcion: str) -> None:
    """CLI común. `parse(raw: bytes, fecha: date) -> list[dict]` levanta `FormatoDesconocido`
    (o cualquier excepción) si el PDF no tiene la forma esperada: ese informe NO se carga ni se
    registra, se reporta como falla y la corrida sigue con el siguiente."""
    import argparse

    ap = argparse.ArgumentParser(prog=f"etl {dataset}", description=descripcion)
    ap.add_argument("--desde", metavar="AAAA-MM",
                    help="primer mes del índice a revisar (backfill). Default: mes actual")
    ap.add_argument("--hasta", metavar="AAAA-MM", help="último mes a revisar (default: actual)")
    ap.add_argument("--max-requests", type=int, metavar="N",
                    help="tope de requests HTTP de esta corrida (backfill en tandas)")
    ap.add_argument("--cache", type=Path, metavar="DIR",
                    help="cache en disco de PDFs e índices de meses cerrados (backfill)")
    ap.add_argument("--sin-red", action="store_true",
                    help="cero requests HTTP: sólo índices y PDFs de --cache (re-parseo tras "
                         "arreglar el parser). Lo que no esté en la cache es una falla")
    ap.add_argument("--archivo", type=Path, metavar="PDF",
                    help="parsear un PDF local (sin HTTP). Con --fecha; sin --url no carga")
    ap.add_argument("--fecha", metavar="AAAA-MM-DD", help="fecha del informe de --archivo")
    ap.add_argument("--url", help="URL con la que registrar --archivo (si se quiere cargar)")
    ap.add_argument("--force", action="store_true",
                    help="volver a procesar informes ya registrados (igual deduplica)")
    args = ap.parse_args(argv)

    rep = report.Report(dataset, "run")
    hoy = dt.date.today()

    if args.archivo:
        if not args.fecha:
            ap.error("--archivo requiere --fecha")
        fecha = dt.date.fromisoformat(args.fecha)
        try:
            filas = parse(args.archivo.read_bytes(), fecha)
        except Exception as e:  # noqa: BLE001
            rep.error(f"{args.archivo.name}: {e}")
            rep.summary()
            return
        rep.info(f"{args.archivo.name}: {len(filas)} filas parseadas")
        if not args.url:
            rep.info("sin --url: sólo parseo, no se carga")
            rep.summary()
            return
        inf = Informe(url=args.url, tipo=tipo, fecha=fecha, nombre=args.archivo.name)
        conn = db.get_conn()
        try:
            n, a = cargar(conn, table=tabla, key_cols=key_cols, filas=filas, date_informe=fecha,
                          fuente=inf.url, extra_cols=extra_cols, rep=rep)
            registrar(conn, tabla_informes, inf, len(filas), n, a)
        finally:
            conn.close()
            rep.summary()
        return

    if args.sin_red and not args.cache:
        ap.error("--sin-red requiere --cache")
    cli = Cliente(args.max_requests, args.cache, hoy, sin_red=args.sin_red)
    conn = db.get_conn()
    try:
        vistos = procesados(conn, tabla_informes)
        ultima = ultima_fecha(conn, tabla_informes)
        hechos = 0
        for mes in meses(args.desde, args.hasta, hoy):
            try:
                informes = [i for i in parse_indice(cli.indice(mes)) if i.tipo == tipo]
            except SinPresupuesto as e:
                rep.info(f"{e}: corte en el índice {mes}; volver a correr para seguir")
                return
            except Exception as e:  # noqa: BLE001 - índice caído o cambiado
                rep.error(f"índice {mes}: {e}")
                return
            if not informes and sin_informes_es_falla(mes, tipo, hoy):
                # Silencioso sería peor que caído: un cambio de nombres/links en el índice deja
                # 0 informes y la corrida "sin informes nuevos" saldría ok para siempre.
                rep.error(f"índice {mes}: 0 informes {tipo} (¿cambió el índice o los nombres?)")
                continue
            nuevos = [i for i in informes if args.force or i.url not in vistos]
            rep.info(f"{mes}: {len(informes)} informe(s) {tipo}, {len(nuevos)} por procesar")
            for inf in nuevos:
                if ultima and inf.fecha < ultima and not args.force:
                    rep.info(f"  ojo: {inf.nombre} es anterior al último procesado ({ultima}); "
                             f"se carga igual (compara contra lo vigente a su fecha; "
                             f"reprocesar los posteriores con --force)")
                try:
                    raw = cli.pdf(inf.url)
                except SinPresupuesto as e:
                    rep.info(f"{e}: quedan informes sin bajar; volver a correr para seguir")
                    return
                except Exception as e:  # noqa: BLE001
                    rep.error(f"{inf.nombre}: bajando el PDF: {e}")
                    continue
                try:
                    filas = parse(raw, inf.fecha)
                except Exception as e:  # noqa: BLE001 - layout inesperado: no se carga NADA
                    rep.error(f"{inf.nombre}: {e}")
                    continue
                n, a = cargar(conn, table=tabla, key_cols=key_cols, filas=filas,
                              date_informe=inf.fecha, fuente=inf.url, extra_cols=extra_cols,
                              rep=rep)
                registrar(conn, tabla_informes, inf, len(filas), n, a)
                vistos.add(inf.url)
                ultima = max(ultima, inf.fecha) if ultima else inf.fecha
                hechos += 1
                rep.info(f"  {inf.fecha}  {inf.nombre}: filas={len(filas)} nuevos={n} "
                         f"actualizados={a}")
        rep.info("sin informes nuevos" if hechos == 0 else f"informes procesados: {hechos}")
    finally:
        conn.close()
        rep.info(f"requests HTTP: {cli.requests}")
        rep.summary()
