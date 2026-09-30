"""Parser del Informe SEMANAL de Estimaciones Agrícolas (MAGyP, PDF): tablas de avance.

Se leen sólo las tablas "AVANCE DE LA SIEMBRA/COSECHA DE <CULTIVO>" (una o más páginas, las
siguientes con "(cont)"). El resto del informe (texto, humedad, series de campañas cerradas) no.

Por qué por coordenadas y no con `extract_tables`
-------------------------------------------------
`extract_tables` de pdfplumber mezcla columnas (las celdas vacías corren los valores). Acá cada
número se asigna a la columna cuyo encabezado le queda más cerca en x, y las columnas se
identifican por el TEXTO del encabezado, no por posición: el layout 2023 no tiene las columnas
de área no cosechada / cosechada, las tablas de siembra traen "ÁREA A SEMBRAR", y en las páginas
"(cont)" la fila de unidades ("Hectáreas") a veces omite una columna.

Estructura de una tabla (2023 y 2026 iguales salvo las columnas de área):

    ÁREA            dd/mm dd/mm ... (6 semanas)   ÁREA NO COSECHADA  ÁREA COSECHADA
    DELEGACIÓN  SEMBRADA  %  %  ...               Hectáreas          Hectáreas
    Bolívar     256.500   7  12 14 17 21 25       76.950             44.888     <- delegación
    ...
    25/26     3.184.877   9  14 ...                                              <- subtotal
    BUENOS AIRES                                   (la etiqueta va en una línea aparte, entre
    24/25     3.130.724   6  13 ...                 la fila de la campaña y la anterior)
    CATAMARCA    14.100   ...                                                    <- provincia sin
    CHACO (Charata) ...                                                             delegaciones
    25/26 ... / TOTAL PAÍS / 24/25 ...                                            <- país

Casos particulares que absorbe el parser:
  - Trigo: "Bahía Blanca T. pan" + "T. fideo" -> trigo_pan / trigo_fideo (sin desglose: trigo).
  - Soja: filas "Bolívar 1ª" + "2ª" (la 2ª hereda el nombre) -> soja_1ra / soja_2da; los
    subtotales y el país son soja total (cultivo 'soja').
  - Arroz: filas LA (largo ancho) / LF (largo fino) con la etiqueta de la zona en una línea
    aparte entre las dos, y totales "T. <ZONA>" -> arroz_la / arroz_lf / arroz.
  - "CHACO (Charata)", "STGO. ESTERO (Quimilí)": delegaciones mostradas a nivel provincia. Y
    "STGO. ESTERO" suelto al lado de "STGO. ESTERO (Quimilí)" también es una delegación.
  - La fila de la campaña ANTERIOR (24/25) de cada subtotal se descarta: ese dato entra por los
    informes de aquella campaña.
  - Celda de % vacía -> no se emite fila (ver config.py: NULL, no 0).
"""
from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass, field

import pdfplumber

from etl.core.magyp_informes import normalizar, slug

# Anclado al inicio de la línea y en mayúsculas: el texto de las delegaciones dice "...el avance
# de la cosecha de maíz de primera," (19/02/2026) y con re.I + search eso pasaba por título.
_RE_TITULO = re.compile(r"^AVANCE DE LA (SIEMBRA|COSECHA) DE (.+?)\s*(\(\s*cont\.?\s*\))?\s*$")
# "CAMPAÑA: 2025/2026 (*)" y también "CAMPAÑA: 2025/26(*)" (maní oct-2025)
_RE_CAMPANIA = re.compile(r"CAMPAÑA:\s*(\d{4})\s*/\s*(\d{4}|\d{2})(?!\d)", re.I)
_RE_DDMM = re.compile(r"^(\d{2})/(\d{2})$")
_RE_CAMP_CORTA = re.compile(r"^(\d{2})/(\d{2})$")
_RE_NUM = re.compile(r"^\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^\d+(?:,\d+)?$")
_RE_VARIEDAD_SOJA = re.compile(r"^([12])\s*[ªº°a]$")

TOL_LINEA = 3.0      # pt: palabras con |top| dentro de esto van en la misma línea
TOL_COLUMNA = 15.0   # pt: distancia máx. del centro de un número al centro de su columna
TOL_FUSION = 6.5    # pt: línea de sólo números que se pega a la fila con nombre (layout 2023)
TOL_FUSION_AMPLIA = 10.0  # pt: idem, sólo si la fila candidata "cierra" (ver _cierra_cosecha)
DIST_ETIQUETA = 7.5  # pt: distancia máx. de una fila sin nombre a su etiqueta flotante
MAX_AREA = 30_000_000

# Cultivos del título -> slug. Uno que no esté acá se slugifica igual (y se avisa).
CULTIVOS = {
    "algodon": "algodon", "arroz": "arroz", "girasol": "girasol", "maiz": "maiz",
    "soja": "soja", "sorgo": "sorgo", "sorgo granifero": "sorgo", "trigo": "trigo",
    "trigo pan": "trigo", "cebada": "cebada", "cebada cervecera": "cebada", "mani": "mani",
    "poroto": "poroto", "poroto seco": "poroto",
}

# Encabezado de columna de área (normalizado) -> variable.
AREAS = {
    "area sembrada": "area_sembrada_ha",
    "area a sembrar": "area_a_sembrar_ha",
    "area no cosechada": "area_no_cosechada_ha",
    "area cosechada": "area_cosechada_ha",
}

PROVINCIAS = {
    "buenos_aires", "catamarca", "chaco", "chubut", "cordoba", "corrientes", "entre_rios",
    "formosa", "jujuy", "la_pampa", "la_rioja", "mendoza", "misiones", "neuquen", "rio_negro",
    "salta", "san_juan", "san_luis", "santa_cruz", "santa_fe", "santiago_del_estero",
    "tierra_del_fuego", "tucuman",
}

_PROV_PEGADAS = {p.replace("_", ""): p for p in PROVINCIAS}

# Grafías distintas de la misma zona (slug -> slug canónico).
ALIAS_ZONA = {
    "stgo_estero": "santiago_del_estero",
    "stgo_del_estero": "santiago_del_estero",
    "sgo_del_estero": "santiago_del_estero",
    "pte_r_s_pena": "pcia_r_s_pena",
    "r_s_pena": "pcia_r_s_pena",
    "pres_r_s_pena": "pcia_r_s_pena",
    "r_del_tala": "rosario_del_tala",
    "gral_pico": "general_pico",
    "total_pais": "total_pais",
}


class FormatoDesconocido(Exception):
    """El PDF no tiene la forma esperada: hay que mirarlo, no adivinar."""


@dataclass
class Columna:
    x: float
    tipo: str            # 'pct' | 'area'
    variable: str        # avance_pct | area_*_ha
    fecha: dt.date | None = None


@dataclass
class Fila:
    """Una fila de datos ya ubicada (antes de clasificar la zona)."""
    nombre: str          # texto de la zona tal como viene ('' si no trae)
    variedad: str | None  # '1', '2' (soja), 'LA', 'LF' (arroz)
    camp_corta: str | None  # '25/26' en las filas de subtotal
    total_t: bool        # arroz: fila "T. <ZONA>"
    valores: dict[int, float]
    top: float


@dataclass
class Tabla:
    fase: str
    cultivo: str
    campania: str        # '2025/26'
    columnas: list[Columna] = field(default_factory=list)
    filas: list[Fila] = field(default_factory=list)


def _num(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def _lineas(words: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(w["top"] - out[-1][0]["top"]) <= TOL_LINEA:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in out]


def _texto(linea: list[dict]) -> str:
    return " ".join(w["text"] for w in linea)


def _centro(w: dict) -> float:
    return (w["x0"] + w["x1"]) / 2


def _pegar_digitos(ws: list[dict]) -> list[dict]:
    """Une fragmentos de un mismo número que pdfplumber separó ('9' '6' -> '96', '1' '00' ->
    '100'): pasa en algunos subtotales de 2025 donde cada dígito es un glifo aparte. Sólo se
    pegan tokens contiguos (hueco < 1 pt); dos números de columnas distintas están a > 10 pt."""
    out: list[dict] = []
    for w in sorted(ws, key=lambda w: w["x0"]):
        if out and w["x0"] - out[-1]["x1"] < 1.0:
            out[-1] = {**out[-1], "text": out[-1]["text"] + w["text"], "x1": w["x1"]}
        else:
            out.append(dict(w))
    return out


def _fecha_corte(dd: int, mm: int, informe: dt.date) -> dt.date:
    """dd/mm sin año: el año del informe, o el anterior si queda en el futuro (dic en enero)."""
    f = dt.date(informe.year, mm, dd)
    if f > informe + dt.timedelta(days=3):
        f = dt.date(informe.year - 1, mm, dd)
    return f


def _columnas(lineas_enc: list[list[dict]], informe: dt.date) -> list[Columna]:
    """Columnas desde el encabezado: '% ' por cada fecha dd/mm y un área por cada 'ÁREA'."""
    cols: list[Columna] = []
    palabras = [w for l in lineas_enc for w in l]
    for w in palabras:
        m = _RE_DDMM.match(w["text"])
        if m:
            cols.append(Columna(_centro(w), "pct", "avance_pct",
                                _fecha_corte(int(m.group(1)), int(m.group(2)), informe)))
    anclas = [w for w in palabras if normalizar(w["text"]) == "area"]
    if not anclas:
        raise FormatoDesconocido("encabezado sin columnas 'ÁREA'")
    for a in anclas:
        xa = _centro(a)
        # Sub-etiqueta: palabras debajo de 'ÁREA' cuyo centro cae a menos de 30 pt.
        util = [w for w in palabras if w is not a
                and not _RE_DDMM.match(w["text"]) and w["text"] not in ("%",)
                and normalizar(w["text"]) not in ("hectareas", "delegacion", "area")]
        # Sub-etiqueta debajo de 'ÁREA' (centro a < 30 pt) o pegada a su derecha en la misma
        # línea ("ÁREA SEMBRADA" en una sola línea, trigo oct-2025).
        sub = [w for w in util if w["top"] > a["top"] + 2 and abs(_centro(w) - xa) < 30]
        x1 = a["x1"]
        for w in sorted((w for w in util if abs(w["top"] - a["top"]) <= 2), key=lambda w: w["x0"]):
            if 0 <= w["x0"] - x1 < 5:
                sub.append(w)
                x1 = w["x1"]
        if x1 != a["x1"]:
            xa = (a["x0"] + x1) / 2  # rótulo en una línea: la columna está centrada en todo él
        etiqueta = normalizar("area " + " ".join(w["text"] for w in sorted(
            sub, key=lambda w: (round(w["top"]), w["x0"]))))
        var = AREAS.get(etiqueta)
        if var is None:
            raise FormatoDesconocido(f"columna de área desconocida: {etiqueta!r}")
        cols.append(Columna(xa, "area", var, informe))
    cols.sort(key=lambda c: c.x)
    if not any(c.tipo == "pct" for c in cols):
        raise FormatoDesconocido("encabezado sin semanas dd/mm")
    return cols


def _cierra_cosecha(cols: list[Columna], valores: dict[int, float]) -> bool:
    """True si la fila trae % de la última semana, área sembrada y área cosechada y cumple
    cosechada = % x (sembrada - no cosechada), con el % redondeado por MAGyP (±0,5).

    Es la cuenta que publica MAGyP (Gral Pico 08/07/2026: 14.073 / (21.980 - 330) = 65,0 %).
    Sin alguna de esas celdas (tablas de siembra, filas sin cosecha) no se puede verificar: False.
    """
    por_var = {cols[j].variable: v for j, v in valores.items() if cols[j].tipo == "area"}
    pcts = [j for j, c in enumerate(cols) if c.tipo == "pct"]
    ultima = max(pcts, key=lambda j: cols[j].fecha)
    if ultima not in valores or "area_sembrada_ha" not in por_var \
            or "area_cosechada_ha" not in por_var:
        return False
    base = por_var["area_sembrada_ha"] - por_var.get("area_no_cosechada_ha", 0.0)
    if base <= 0:
        return False
    return abs(100 * por_var["area_cosechada_ha"] / base - valores[ultima]) <= 0.5


def _pagina(page, informe: dt.date) -> Tabla | None:
    """Tabla de avance de una página (None si la página no es una)."""
    words = page.extract_words()
    lineas = _lineas(words)
    titulo = None
    i_titulo = None
    for i, l in enumerate(lineas[:6]):
        m = _RE_TITULO.search(_texto(l))
        if m:
            titulo, i_titulo = m, i
            break
    if titulo is None:
        return None
    campania = None
    for l in lineas[i_titulo:i_titulo + 3]:
        m = _RE_CAMPANIA.search(_texto(l))
        if m:
            campania = f"{m.group(1)}/{m.group(2)[-2:]}"
    if campania is None:
        raise FormatoDesconocido(f"p{page.page_number}: tabla sin 'CAMPAÑA: AAAA/AAAA'")
    fase = titulo.group(1).lower()
    # El informe del 30/10/2025 trae la campaña pegada al cultivo en el título ("MAÍZ 25/26"):
    # sin este recorte el slug quedaba 'maiz_25'. La campaña se lee aparte (_RE_CAMPANIA).
    cultivo_txt = re.sub(r"\s+\d[\d/\s-]*$", "", normalizar(titulo.group(2)))
    cultivo = CULTIVOS.get(cultivo_txt, slug(cultivo_txt))

    # Fila de unidades: la que tiene '%' (cierra el encabezado).
    i_unid = next((i for i, l in enumerate(lineas) if i > i_titulo
                   and sum(w["text"] == "%" for w in l) >= 2), None)
    if i_unid is None:
        raise FormatoDesconocido(f"p{page.page_number}: tabla sin fila de unidades (%)")
    cols = _columnas(lineas[i_titulo + 2:i_unid + 1], informe)
    tabla = Tabla(fase=fase, cultivo=cultivo, campania=campania, columnas=cols)
    x_etiqueta = min(c.x for c in cols) - 20  # a la izquierda de esto, texto de la zona

    crudas: list[tuple[list[dict], dict[int, float], float]] = []
    flotantes: list[tuple[float, str]] = []
    entradas: list[dict] = []
    for l in lineas[i_unid + 1:]:
        txt = _texto(l)
        if txt.startswith("(*)") or normalizar(txt).startswith("cifras provisorias"):
            break
        etiqueta = [w for w in l if w["x1"] <= x_etiqueta]
        resto = _pegar_digitos([w for w in l if w["x1"] > x_etiqueta
                                and w["text"] not in ("-", "–")])  # '-' = celda vacía
        valores: dict[int, float] = {}
        for w in resto:
            if not _RE_NUM.match(w["text"]):
                raise FormatoDesconocido(
                    f"p{page.page_number}: texto inesperado en zona de números: {txt!r}")
            xc = _centro(w)
            j = min(range(len(cols)), key=lambda k: abs(cols[k].x - xc))
            if abs(cols[j].x - xc) > TOL_COLUMNA:
                raise FormatoDesconocido(
                    f"p{page.page_number}: número sin columna ({w['text']} x={xc:.0f}): {txt!r}")
            if j in valores:
                raise FormatoDesconocido(
                    f"p{page.page_number}: dos números en la misma columna: {txt!r}")
            valores[j] = _num(w["text"])
        entradas.append({"etiqueta": etiqueta, "valores": valores, "top": l[0]["top"]})

    # Layout 2023: en los subtotales de celda alta los % quedan ~4 pt más arriba que el nombre y
    # el área, y salen como una línea aparte SIN nombre. Se pegan a la línea con nombre más
    # cercana (<= TOL_FUSION) si no pisan sus columnas.
    for e in [e for e in entradas if not e["etiqueta"] and e["valores"]]:
        cands = [o for o in entradas if o is not e and o["etiqueta"]
                 and abs(o["top"] - e["top"]) <= TOL_FUSION
                 and not set(o["valores"]) & set(e["valores"])]
        if not cands:
            # 08/07/2026 (girasol, "Gral Pico"): los % en negrita quedan 7 pt debajo del nombre,
            # a caballo de la línea que separa la fila siguiente (6,8 pt de "Santa Rosa"). La
            # geometría no decide: se acepta la fila sólo si con esos % cierra su propia cuenta
            # de cosecha, y tiene que ser UNA sola.
            cerca = [o for o in entradas if o is not e and o["etiqueta"]
                     and abs(o["top"] - e["top"]) <= TOL_FUSION_AMPLIA
                     and not set(o["valores"]) & set(e["valores"])]
            cands = [o for o in cerca if _cierra_cosecha(cols, {**o["valores"], **e["valores"]})]
            if len(cands) != 1:
                raise FormatoDesconocido(
                    f"p{page.page_number}: números sin fila (y={e['top']:.0f}; "
                    f"{len(cerca)} fila(s) cerca, {len(cands)} cierran la cuenta de cosecha)")
        o = min(cands, key=lambda o: abs(o["top"] - e["top"]))
        o["valores"].update(e["valores"])
        e["valores"] = {}
    for e in entradas:
        if e["valores"]:
            crudas.append((e["etiqueta"], e["valores"], e["top"]))
        elif e["etiqueta"]:
            flotantes.append((e["top"], " ".join(w["text"] for w in e["etiqueta"])))

    previo: Fila | None = None
    for etiqueta, valores, top in crudas:
        toks = [w["text"] for w in etiqueta]
        camp_corta = None
        if toks and _RE_CAMP_CORTA.match(toks[-1]):
            # Normalmente '25/26' va solo y el rótulo flota entre el par; desde nov-2025 a
            # veces va en la MISMA línea que el rótulo ("BUENOS AIRES 25/26", "TOTAL PAÍS
            # 25/26") y la fila '24/25' de abajo queda sin nombre (ver más abajo).
            camp_corta, toks = toks[-1], toks[:-1]
        variedad = None
        if len(toks) >= 2 and toks[-2] in ("T.", "T") and normalizar(toks[-1]) in ("pan", "fideo"):
            variedad, toks = normalizar(toks[-1]), toks[:-2]  # trigo: "Bahía Blanca T. pan"
        elif toks and toks[-1] in ("LA", "LF"):
            variedad, toks = toks[-1], toks[:-1]
        elif toks and _RE_VARIEDAD_SOJA.match(toks[-1]):
            variedad, toks = _RE_VARIEDAD_SOJA.match(toks[-1]).group(1), toks[:-1]
        total_t = bool(toks) and toks[0] in ("T.", "T")
        if total_t:
            toks = toks[1:]
        nombre = " ".join(toks)
        if not nombre and not total_t:
            cerca = [(abs(t - top), n) for t, n in flotantes if abs(t - top) <= DIST_ETIQUETA]
            if cerca:
                nombre = min(cerca)[1]
            elif previo is not None and variedad in ("2", "LF", "fideo"):
                nombre = previo.nombre  # "2ª" / "LF" debajo de la fila con el nombre
            elif (camp_corta is not None and previo is not None and previo.nombre
                  and previo.camp_corta is not None and previo.variedad == variedad
                  and (int(previo.camp_corta[:2]) - int(camp_corta[:2])) % 100 == 1):
                # "BUENOS AIRES 25/26" y debajo "24/25" sin nombre: es la otra mitad del par
                # del mismo subtotal (campañas consecutivas, filas contiguas).
                nombre = previo.nombre
            else:
                raise FormatoDesconocido(
                    f"p{page.page_number}: fila sin nombre de zona (y={top:.0f})")
        fila = Fila(nombre=nombre, variedad=variedad, camp_corta=camp_corta, total_t=total_t,
                    valores=valores, top=top)
        tabla.filas.append(fila)
        previo = fila
    return tabla


def _zona(nombre: str) -> tuple[str, str | None, bool]:
    """(zona_slug, provincia_slug_si_viene_entre_paréntesis, es_mayúscula)."""
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", nombre)
    if m:
        prov = slug(m.group(1))
        return (ALIAS_ZONA.get(slug(m.group(2)), slug(m.group(2))),
                ALIAS_ZONA.get(prov, prov), False)
    letras = [c for c in nombre if c.isalpha()]
    mayus = bool(letras) and all(c.isupper() for c in letras)
    z = slug(nombre)
    z = _PROV_PEGADAS.get(z, z)  # 'SANLUIS' (2023) -> san_luis
    return ALIAS_ZONA.get(z, z), None, mayus


def _filas_campania_actual(t: Tabla, corta: str) -> set[int]:
    """ids de las filas de subtotal que son de la campaña de la tabla.

    Cada subtotal viene de a pares: campaña actual arriba, anterior abajo. Normalmente el rótulo
    ('25/26') lo dice; pero MAGyP a veces deja el rótulo viejo en una tabla nueva (arroz siembra
    2025/26 en sept-2025: '24/25' / '23/24'). Se acepta por POSICIÓN sólo si el par es de
    campañas consecutivas; un subtotal suelto tiene que traer el rótulo correcto.
    """
    grupos: dict[str, list[Fila]] = {}
    for f in t.filas:
        if f.camp_corta is not None:
            grupos.setdefault(f.nombre, []).append(f)
    out: set[int] = set()
    for nombre, fs in grupos.items():
        fs = sorted(fs, key=lambda f: f.top)
        buenas = [f for f in fs if f.camp_corta == corta]
        if buenas:
            out.update(id(f) for f in buenas)
            continue
        if len(fs) == 2:
            y0 = int(fs[0].camp_corta[:2])
            y1 = int(fs[1].camp_corta[:2])
            if (y0 - y1) % 100 == 1:
                out.add(id(fs[0]))
                continue
        raise FormatoDesconocido(f"{t.fase} {t.cultivo} {t.campania}: subtotal {nombre!r} sin "
                                 f"fila de la campaña ({[f.camp_corta for f in fs]})")
    return out


def filas_tabla(t: Tabla) -> list[dict]:
    """Clasifica las filas de una tabla (ya unida con sus '(cont)') y emite filas long."""
    corta = t.campania[2:4] + "/" + t.campania[5:7]
    # "STGO. ESTERO" suelto es delegación si en la tabla también está "STGO. ESTERO (Quimilí)".
    # Se compara la grafía cruda (sin alias): "SANTIAGO DEL ESTERO" junto a "Stgo. del Estero
    # (Quimilí)" (algodón) sí es el subtotal provincial.
    con_parentesis = {slug(f.nombre.split("(")[0]) for f in t.filas if "(" in f.nombre}
    actuales = _filas_campania_actual(t, corta)
    out: list[dict] = []
    pendientes: list[dict] = []
    vio_pais = False
    for f in t.filas:
        if f.camp_corta is not None and id(f) not in actuales:
            continue  # campaña anterior: se descarta
        zona, prov_par, mayus = _zona(f.nombre)
        if zona == "total_pais":
            tipo, provincia = "pais", None
            vio_pais = vio_pais or f.variedad is None
        elif prov_par is not None:
            tipo, provincia = "delegacion", prov_par
        elif mayus and zona in PROVINCIAS and slug(f.nombre) in con_parentesis:
            tipo, provincia = "delegacion", zona  # "STGO. ESTERO" junto a "(Quimilí)"
        elif mayus and zona in PROVINCIAS:
            tipo, provincia = "provincia", zona
        else:
            tipo, provincia = "delegacion", None

        cultivo = t.cultivo
        if f.variedad in ("1", "2"):
            cultivo = f"{t.cultivo}_{'1ra' if f.variedad == '1' else '2da'}"
        elif f.variedad in ("pan", "fideo"):
            cultivo = f"{t.cultivo}_{f.variedad}"
        elif f.variedad in ("LA", "LF"):
            cultivo = f"{t.cultivo}_{f.variedad.lower()}"

        emitidas = []
        for j, v in f.valores.items():
            c = t.columnas[j]
            if c.tipo == "pct" and not 0 <= v <= 100:
                raise FormatoDesconocido(f"{t.cultivo} {f.nombre}: % fuera de rango: {v}")
            if c.tipo == "area" and not 0 <= v <= MAX_AREA:
                raise FormatoDesconocido(f"{t.cultivo} {f.nombre}: área fuera de rango: {v}")
            emitidas.append({
                "cultivo": cultivo, "campania": t.campania, "zona_tipo": tipo, "zona": zona,
                "fase": t.fase, "variable": c.variable, "fecha_corte": c.fecha, "valor": v,
                "provincia": provincia, "zona_fuente": f.nombre,
            })
        out.extend(emitidas)
        if tipo == "delegacion" and provincia is None:
            pendientes.extend(emitidas)
        elif tipo == "provincia":
            for p in pendientes:
                p["provincia"] = provincia
            pendientes = []
    if not vio_pais:
        raise FormatoDesconocido(f"{t.fase} {t.cultivo} {t.campania}: sin fila TOTAL PAÍS")
    return out


def parse_pdf(raw: bytes, informe: dt.date) -> list[dict]:
    """Filas long de todas las tablas de avance del informe. Levanta FormatoDesconocido."""
    tablas: list[Tabla] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            t = _pagina(page, informe)
            if t is None:
                continue
            prev = tablas[-1] if tablas else None
            if prev and (prev.fase, prev.cultivo, prev.campania) == (t.fase, t.cultivo,
                                                                      t.campania):
                prev.filas.extend(t.filas)  # página "(cont)"
                if [(c.tipo, c.variable, c.fecha) for c in prev.columnas] != \
                        [(c.tipo, c.variable, c.fecha) for c in t.columnas]:
                    # Las columnas de la (cont) se usan con los índices de SU página: se
                    # traducen a los de la primera por (variable, fecha).
                    mapa = {}
                    for j, c in enumerate(t.columnas):
                        k = next((k for k, p in enumerate(prev.columnas)
                                  if (p.variable, p.fecha) == (c.variable, c.fecha)), None)
                        if k is None:
                            raise FormatoDesconocido(
                                f"{t.cultivo}: la página (cont) trae otra columna {c.variable}")
                        mapa[j] = k
                    for f in t.filas:
                        f.valores = {mapa[j]: v for j, v in f.valores.items()}
            else:
                tablas.append(t)
    if not tablas:
        raise FormatoDesconocido("el informe no trae tablas de avance de siembra/cosecha")
    out: list[dict] = []
    for t in tablas:
        out.extend(filas_tabla(t))
    claves = [(r["cultivo"], r["campania"], r["zona_tipo"], r["zona"], r["fase"],
               r["variable"], r["fecha_corte"]) for r in out]
    if len(claves) != len(set(claves)):
        dup = sorted({k for k in claves if claves.count(k) > 1})[:3]
        raise FormatoDesconocido(f"claves duplicadas en el informe: {dup}")
    return out
