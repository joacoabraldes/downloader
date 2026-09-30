"""Parser del Informe MENSUAL de Estimaciones Agrícolas (MAGyP, PDF): superficie y producción
NACIONAL por cultivo.

Cada cultivo (TRIGO, CEBADA, GIRASOL, MAÍZ, SOJA, SORGO GRANÍFERO, ARROZ, MANÍ, ALGODÓN) trae
una o dos tablitas "Campaña AAAA/AA" con esta forma:

                          Campaña 26/27              Sep 26 vs  Sep 26 vs
               Campaña    Ago 26    Sep 26            Ago 26     25/26
               25/26
    Superficie
    implantada (ha)  6.970.000 6.600.000 6.600.000    0%         5,3%
    Producción
    estimada (ton)  27.900.000   -         -          -          -

  col0 = campaña ANTERIOR (valor vigente hoy), col1 = estimación del mes anterior, col2 = la
  estimación de ESTE informe, col3/col4 = variaciones % (sin signo).

El layout es frágil (etiquetas partidas en dos o tres líneas, números que quedan a media
altura, encabezados en otro orden en soja), así que se parsea por coordenadas y se valida TODO:

  - las 5 columnas se identifican por el TEXTO del encabezado (campaña anterior, mes anterior,
    mes del informe, dos columnas "vs"); el mes de col2 tiene que ser el del informe;
  - cada etiqueta de fila tiene que ser conocida (ETIQUETAS);
  - la variación % contra la campaña anterior (col4) tiene que cerrar con col0 y col2
    (tolerando el redondeo de los valores publicados): valida que cayeron en su columna. La de
    col3 (vs mes anterior) no se valida: col1 no se guarda y MAGyP a veces deja ahí un % viejo;
  - rangos plausibles de superficie, producción y rinde implícito;
  - están los 9 cultivos esperados, cada uno con al menos una tabla;
  - si la misma (cultivo, campaña, variable) sale de dos tablas (col2 de su tabla y col0 de la
    tabla de la campaña siguiente), tienen que coincidir.

Ante CUALQUIER discrepancia se levanta FormatoDesconocido y el informe no se carga: mejor una
falla ruidosa que números equivocados en la base.
"""
from __future__ import annotations

import datetime as dt
import io
import re

import pdfplumber

from etl.core.magyp_informes import normalizar

# Encabezado de sección (línea sola, margen izquierdo) -> slug del cultivo.
CULTIVOS = {
    "trigo": "trigo", "cebada": "cebada", "girasol": "girasol", "maiz": "maiz",
    "soja": "soja", "sorgo granifero": "sorgo", "sorgo": "sorgo", "arroz": "arroz",
    "mani": "mani", "algodon": "algodon",
}
ESPERADOS = {"trigo", "cebada", "girasol", "maiz", "soja", "sorgo", "arroz", "mani", "algodon"}

# Etiqueta de fila normalizada (sin espacios) -> variable. Se matchea por prefijo.
ETIQUETAS = [
    ("superficiedestinadaagranocosechada", "superficie_grano_ha"),
    ("superficiesilajes", "superficie_silaje_otros_ha"),
    ("superficieaimplantar", "superficie_a_implantar_ha"),  # intención (antes de sembrar)
    ("superficieasembrar", "superficie_a_implantar_ha"),
    ("superficieimplantada", "superficie_implantada_ha"),
    ("superficiesembrada", "superficie_implantada_ha"),  # sinónimo en algunos meses
    ("superficiecosechada", "superficie_cosechada_ha"),
    ("produccionestimada", "produccion_t"),
    ("produccion", "produccion_t"),
]

RANGOS = {  # plausibilidad a nivel país
    "superficie_implantada_ha": (50_000, 30_000_000),
    "superficie_a_implantar_ha": (50_000, 30_000_000),
    "superficie_grano_ha": (50_000, 30_000_000),
    "superficie_silaje_otros_ha": (10_000, 10_000_000),
    "superficie_cosechada_ha": (50_000, 30_000_000),
    "produccion_t": (100_000, 200_000_000),
}
RINDE_KG_HA = (500, 15_000)  # producción / superficie implantada

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sep": 9, "sept": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

_RE_PEGADO = re.compile(r"^([A-Za-z]{3,4}\.?)?(\d{2})?(vs)?$")
_RE_TABLA = re.compile(r"^campana (\d{4})/(\d{2})$")
_RE_CAMP_CORTA = re.compile(r"^(\d{2})/(\d{2})$")
_RE_NUM = re.compile(r"^\d{1,3}(?:\.\d{3})+$|^\d+$")
# Número recortado por la celda: "6.500.00" (trigo, col0, casi todos los informes de 2025-26).
_RE_RECORTADO = re.compile(r"^\d{1,3}(?:\.\d{3})*\.\d{1,2}$")
# "5,3%", "0%", y también "21.1%" (punto decimal) y "2,8" (sin %), vistos en 2025.
_RE_PCT = re.compile(r"^-?\d+(?:[.,]\d+)?%$|^-?\d+,\d+$")
X_ETIQUETA = 175.0   # a la izquierda: texto de la etiqueta de fila
TOL_LINEA = 3.0
TOL_CLUSTER = 22.0   # pt entre centros de palabras del encabezado de una misma columna
TOL_COLUMNA = 25.0
TOL_FILA = 6.0
MARGEN_SUP = 60.0    # pt: encabezado de página (número, "ESTIMACIONES AGRÍCOLAS Informe ...")


class FormatoDesconocido(Exception):
    """El PDF no tiene la forma esperada: hay que mirarlo, no adivinar."""


def _lineas(words):
    # Las variaciones % vienen con un ícono de flecha pegado (glifo de uso privado, p.ej.
    # U+F088): se descarta, el signo no se usa (la validación compara en valor absoluto).
    for w in words:
        w["text"] = re.sub("[\ue000-\uf8ff]", "", w["text"])
    words = [w for w in words if w["text"]]
    # Encabezados pegados: "Oct25" (cebada oct-2025), "26vs" (sorgo feb-2026) -> tokens aparte
    # con la misma posición (para el clustering de columnas sólo importa el centro).
    partidas = []
    for w in words:
        m = _RE_PEGADO.match(w["text"])
        if m and sum(g is not None for g in m.groups()) > 1:
            partidas.extend({**w, "text": g} for g in m.groups() if g is not None)
        else:
            partidas.append(w)
    words = partidas
    out = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(w["top"] - out[-1][0]["top"]) <= TOL_LINEA:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in out]


def _c(w):
    return (w["x0"] + w["x1"]) / 2


def _es_valor(t: str) -> bool:
    return bool(_RE_NUM.match(t) or _RE_RECORTADO.match(t) or _RE_PCT.match(t) or t == "-")


def _es_encabezado(t: str) -> bool:
    n = normalizar(t).strip(".")
    return (n in ("campana", "vs") or n in MESES or bool(re.match(r"^\d{2}$", n))
            or bool(_RE_CAMP_CORTA.match(n)))


def _anterior(camp: str) -> str:
    y = int(camp[:4])
    return f"{y - 1}/{str(y)[2:]}"


def _columnas(enc: list[dict], campania: str, informe: dt.date, ctx: str) -> list[float]:
    """x de las 5 columnas [camp_ant, mes_ant, mes_actual, vs_mes, vs_camp] validando textos."""
    corta = campania[2:4] + "/" + campania[5:7]
    ant = _anterior(campania)
    corta_ant = ant[2:4] + "/" + ant[5:7]
    # Fuera el súper-encabezado "Campaña 26/27" (la campaña de la tabla, pegada a su "Campaña").
    # Sólo ese: un "vs 24/25" en col4 de la tabla 2024/25 (rótulo viejo, maíz ago-2025) queda.
    quitar = set()
    for w in enc:
        if w["text"] == corta:
            izq = [o for o in enc if abs(o["top"] - w["top"]) <= TOL_LINEA
                   and w["x0"] - 8 <= o["x1"] <= w["x0"] + 1
                   and normalizar(o["text"]) == "campana"]
            if izq:
                quitar.add(id(w))
                quitar.add(id(max(izq, key=lambda o: o["x1"])))
    ws = sorted((w for w in enc if id(w) not in quitar), key=_c)
    clusters: list[list[dict]] = []
    for w in ws:
        if clusters and _c(w) - _c(clusters[-1][-1]) <= TOL_CLUSTER:
            clusters[-1].append(w)
        else:
            clusters.append([w])
    if len(clusters) != 5:
        raise FormatoDesconocido(f"{ctx}: encabezado con {len(clusters)} columnas (se esperan 5): "
                                 f"{[[w['text'] for w in c] for c in clusters]}")

    def meses_de(cl):
        out = []
        ts = sorted(cl, key=lambda w: (round(w["top"]), w["x0"]))
        for i, w in enumerate(ts):
            m = MESES.get(normalizar(w["text"]).strip("."))
            if not m:
                continue
            # el año es el siguiente "\d{2}" (saltando basura como el "2r" de jun-2026)
            sig = next((o for o in ts[i + 1:i + 3] if re.match(r"^\d{2}$", o["text"])), None)
            if sig is not None:
                out.append((2000 + int(sig["text"]), m))
        return out

    textos = [{w["text"] for w in cl} for cl in clusters]
    vs = ["vs" in t for t in textos]
    if vs != [False, False, False, True, True]:
        raise FormatoDesconocido(f"{ctx}: las columnas 'vs' no están donde se esperan: {textos}")
    if corta_ant not in textos[0]:
        raise FormatoDesconocido(f"{ctx}: col0 no es la campaña anterior {corta_ant}: {textos[0]}")
    m1, m2 = meses_de(clusters[1]), meses_de(clusters[2])
    if len(m1) != 1 or len(m2) != 1:
        raise FormatoDesconocido(f"{ctx}: meses de col1/col2 ilegibles: {textos[1]} {textos[2]}")
    if m2[0] != (informe.year, informe.month):
        raise FormatoDesconocido(f"{ctx}: col2 es {m2[0]}, no el mes del informe {informe}")
    if not m1[0] < m2[0]:
        raise FormatoDesconocido(f"{ctx}: col1 {m1[0]} no es anterior a col2 {m2[0]}")
    # col4 sólo tiene que ser "vs <campaña>": su rótulo a veces queda viejo (cebada ago-2026 dice
    # 'vs 24/25' en la tabla 2026/27). Lo que manda es col0, y el % de col4 se valida contra col0.
    if not any(_RE_CAMP_CORTA.match(t) for t in textos[4]):
        raise FormatoDesconocido(f"{ctx}: col4 no compara contra una campaña: {textos[4]}")
    return [sum(_c(w) for w in cl) / len(cl) for cl in clusters]


def _num(t: str) -> float | None:
    if t == "-":
        return None
    if t.endswith("%"):
        return abs(float(t[:-1].replace(",", ".")))
    if _RE_RECORTADO.match(t):  # "6.500.00" -> 6.500.000: se completa con ceros (ver _tabla)
        cab, ult = t.rsplit(".", 1)
        return float((cab + ult.ljust(3, "0")).replace(".", ""))
    return float(t.replace(".", ""))


def _unidad(v: float) -> float:
    """Unidad de redondeo de un valor publicado: 17.400.000 -> 100.000, 690.000 -> 10.000."""
    n, u = int(v), 1
    while n and n % 10 == 0 and u < 1_000_000:
        n //= 10
        u *= 10
    return u


def _check_pct(a, b, pct_txt: str, ctx: str) -> None:
    """|b/a - 1| tiene que coincidir con el % publicado.

    MAGyP calcula el % con los valores SIN redondear y publica los redondeados (algodón dic-2025:
    690.000 -> 560.000 publicado como 18,0%, con los redondeados da 18,8%), así que la tolerancia
    suma el redondeo del % y el de cada valor (media unidad sobre el valor)."""
    if a is None or b is None or pct_txt == "-" or a == 0:
        return
    t = pct_txt.rstrip("%").lstrip("-").replace(",", ".")
    pct = float(t)
    decs = len(t.split(".")[1]) if "." in t else 0
    tol = (0.5 * 10 ** -decs + 0.05
           + 100 * (_unidad(a) / 2 / a + _unidad(b) / 2 / b) * (b / a))
    real = abs(b / a - 1) * 100
    if abs(real - pct) > tol:
        raise FormatoDesconocido(f"{ctx}: variación publicada {pct_txt} no cierra con "
                                 f"{a:,.0f} -> {b:,.0f} ({real:.2f}%)")


def _tabla(lineas, cultivo, campania, informe, ctx) -> list[dict] | None:
    """Parsea una tablita a partir de las líneas que siguen al título 'Campaña AAAA/AA'."""
    enc: list[dict] = []
    filas: list[dict] = []  # {etiqueta:[words], top, bottom, valores:[words]}
    sueltos: list[dict] = []
    for n, l in enumerate(lineas):
        txt = normalizar(" ".join(w["text"] for w in l))
        if l[0]["x0"] <= 75 and (txt in CULTIVOS or _RE_TABLA.match(txt)):
            break  # empieza otra sección u otra tabla
        izq = [w for w in l if w["x1"] <= X_ETIQUETA and not _es_valor(w["text"])]
        der = [w for w in l if w not in izq]
        # Basura suelta en el encabezado ("May 2r 26", jun-2026): un token de <= 2 caracteres
        # antes de las filas no cuenta como texto. Igual el encabezado se valida entero.
        prosa = [w for w in der if not _es_valor(w["text"]) and not _es_encabezado(w["text"])
                 and not (not filas and len(w["text"]) <= 2)]
        if prosa or (izq and not filas and normalizar(izq[0]["text"]) not in
                     ("superficie", "produccion", "campana")):
            # Texto antes del encabezado (girasol jul-2025: el párrafo va entre el título y la
            # tabla): se saltea, salvo que empiece otra sección o pase demasiado.
            if not enc and not filas and n < 25:
                continue
            break  # arrancó el texto: fin de la tabla (o no había tabla)
        if izq and normalizar(izq[0]["text"]) in ("superficie", "produccion"):
            filas.append({"etiqueta": list(izq), "top": l[0]["top"],
                          "bottom": max(w["bottom"] for w in l), "valores": []})
        elif izq and filas:
            filas[-1]["etiqueta"].extend(izq)
            filas[-1]["bottom"] = max(filas[-1]["bottom"], max(w["bottom"] for w in izq))
        elif izq:  # "Campaña" del encabezado a la izquierda
            enc.extend(izq)
        if not filas:
            enc.extend(der)
        else:
            sueltos.extend(w for w in der if _es_valor(w["text"]))
    if not filas:
        return None
    cols = _columnas(enc, campania, informe, ctx)
    for w in sueltos:
        yc = (w["top"] + w["bottom"]) / 2
        dentro = [f for f in filas if f["top"] - TOL_FILA <= yc <= f["bottom"] + TOL_FILA]
        if not dentro:
            raise FormatoDesconocido(f"{ctx}: valor {w['text']} sin fila")
        f = min(dentro, key=lambda f: abs((f["top"] + f["bottom"]) / 2 - yc))
        f["valores"].append(w)

    out = []
    for f in filas:
        etiqueta = " ".join(w["text"] for w in sorted(f["etiqueta"],
                                                         key=lambda w: (round(w["top"]), w["x0"])))
        clave = re.sub(r"[^a-z]", "", normalizar(etiqueta))
        var = next((v for p, v in ETIQUETAS if clave.startswith(p)), None)
        if var is None:
            raise FormatoDesconocido(f"{ctx}: etiqueta de fila desconocida {etiqueta!r}")
        # "Superficie a implantar" es la intención de la campaña de la tabla (col2); en col0 la
        # campaña anterior ya está implantada: ese valor es superficie_implantada_ha.
        var_ant = "superficie_implantada_ha" if var == "superficie_a_implantar_ha" else var
        celdas: dict[int, str] = {}
        for w in f["valores"]:
            j = min(range(5), key=lambda k: abs(cols[k] - _c(w)))
            if abs(cols[j] - _c(w)) > TOL_COLUMNA:
                raise FormatoDesconocido(f"{ctx} {var}: {w['text']} fuera de columna")
            if j in celdas:
                raise FormatoDesconocido(f"{ctx} {var}: dos valores en la columna {j}")
            celdas[j] = w["text"]
        for j in (0, 1, 2):
            if j in celdas and not (_RE_NUM.match(celdas[j]) or celdas[j] == "-"
                                    or _RE_RECORTADO.match(celdas[j])):
                raise FormatoDesconocido(f"{ctx} {var}: col{j} no es un número: {celdas[j]}")
        # Un número recortado ("6.500.00") se completa con ceros SÓLO si una variación %
        # publicada lo involucra y cierra (error posible: < 10 unidades sobre millones).
        for j in (0, 1, 2):
            if j in celdas and _RE_RECORTADO.match(celdas[j]):
                otra = celdas.get(2 if j == 0 else 0, "-")
                if (j == 1 or celdas.get(4, "-") == "-" or otra == "-"
                        or _RE_RECORTADO.match(otra)):
                    raise FormatoDesconocido(f"{ctx} {var}: número recortado {celdas[j]!r} "
                                             f"sin variación % para validarlo")
        for j in (3, 4):
            if j in celdas and not (_RE_PCT.match(celdas[j]) or celdas[j] == "-"):
                raise FormatoDesconocido(f"{ctx} {var}: col{j} no es un %: {celdas[j]}")
        v = {j: _num(celdas[j]) if j in celdas else None for j in (0, 1, 2)}
        # col3 (vs mes anterior) NO se valida: col1 no se guarda, y MAGyP a veces deja ahí un %
        # viejo (maíz ago-2026: 9.900.000 -> 9.900.000 "2,1%"). col4 sí: valida col0 y col2.
        _check_pct(v[0], v[2], celdas.get(4, "-"), f"{ctx} {var} vs campaña anterior")
        for j, camp, origen, vr in ((2, campania, "estimacion", var),
                                    (0, _anterior(campania), "campania_anterior", var_ant)):
            if v[j] is None:
                continue
            lo, hi = RANGOS[vr]
            if not lo <= v[j] <= hi:
                raise FormatoDesconocido(f"{ctx} {var} {camp}: {v[j]:,.0f} fuera de rango")
            out.append({"cultivo": cultivo, "campania": camp, "variable": vr, "valor": v[j],
                        "origen": origen, "etiqueta": etiqueta})
    return out


def parse_pdf(raw: bytes, informe: dt.date) -> list[dict]:
    """Filas (cultivo, campania, variable, valor, origen, etiqueta). Levanta FormatoDesconocido."""
    crudas: list[dict] = []
    tablas_por_cultivo: dict[str, int] = {}
    # Todo el documento como UNA secuencia de líneas: una tablita puede quedar partida entre dos
    # páginas (maní jun-2026: encabezado al pie de una, filas en la siguiente). Se descarta el
    # encabezado de página (número y "ESTIMACIONES AGRÍCOLAS Informe Mensual ...", top < 60) y se
    # corre `top` por la altura acumulada para que el orden vertical siga valiendo.
    palabras: list[dict] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        off = 0.0
        for page in pdf.pages:
            for w in page.extract_words():
                if w["top"] < MARGEN_SUP:
                    continue
                palabras.append({**w, "top": w["top"] + off, "bottom": w["bottom"] + off,
                                 "pg": page.page_number})
            off += float(page.height)
    lineas = _lineas(palabras)
    cultivo = None
    for i, l in enumerate(lineas):
        if l[0]["x0"] > 75:
            continue
        txt = normalizar(" ".join(w["text"] for w in l))
        if txt in CULTIVOS:
            cultivo = CULTIVOS[txt]
            tablas_por_cultivo.setdefault(cultivo, 0)
            continue
        m = _RE_TABLA.match(txt)
        if not m or cultivo is None:
            continue
        campania = f"{m.group(1)}/{m.group(2)}"
        if int(m.group(2)) != (int(m.group(1)) + 1) % 100:
            raise FormatoDesconocido(f"{cultivo}: campaña rara {campania}")
        ctx = f"p{l[0]['pg']} {cultivo} {campania}"
        filas = _tabla(lineas[i + 1:], cultivo, campania, informe, ctx)
        if filas:
            tablas_por_cultivo[cultivo] += 1
            crudas.extend(filas)
    faltan = sorted(c for c in ESPERADOS if not tablas_por_cultivo.get(c))
    if faltan:
        raise FormatoDesconocido(f"cultivos sin tabla en el informe: {faltan}")

    # Una sola fila por clave: manda la estimación de su propia tabla; col0 de la tabla de la
    # campaña siguiente sólo completa lo que falta, y si están las dos tienen que coincidir.
    out: dict[tuple, dict] = {}
    for f in sorted(crudas, key=lambda f: f["origen"] != "estimacion"):
        k = (f["cultivo"], f["campania"], f["variable"])
        if k in out:
            if abs(out[k]["valor"] - f["valor"]) > 0.5:
                raise FormatoDesconocido(f"{k}: {out[k]['valor']:,.0f} ({out[k]['origen']}) vs "
                                         f"{f['valor']:,.0f} ({f['origen']})")
            continue
        out[k] = f
    # Rinde implícito: producción / superficie implantada de la misma campaña.
    for (c, camp, var), f in out.items():
        if var != "produccion_t":
            continue
        sup = out.get((c, camp, "superficie_implantada_ha"))
        if sup:
            r = f["valor"] * 1000 / sup["valor"]
            if not RINDE_KG_HA[0] <= r <= RINDE_KG_HA[1]:
                raise FormatoDesconocido(f"{c} {camp}: rinde implícito {r:,.0f} kg/ha fuera de rango")
    return list(out.values())
