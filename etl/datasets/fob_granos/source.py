"""Fuente MAGyP: precios FOB oficiales diarios (API JSON, un request por día).

La API está documentada en
`/sitio/areas/ss_mercados_agropecuarios/fob_oficiales/_archivos/000021_Precios Fob Api.php` y
toma un único parámetro `Fecha=dd/mm/aaaa`. Dos particularidades que definen todo este módulo:

  - NO hay endpoint de rango ni de histórico: un día por request. El histórico completo
    (1993-01 -> hoy) son ~8.500 días hábiles. De ahí que exista `load_history` con pausa
    explícita y que la ingesta guarde todas las filas que trae cada día (ver config).

  - El host canónico `monitorsiogranos.magyp.gob.ar` devuelve 403 a todo (probado 2026-09-11).
    La propia página de la API avisa "debido a situaciones técnicas utilizar el siguiente link"
    y apunta al espejo bajo www.magyp.gob.ar, que es el que sí responde. Es el MISMO host que
    usan granos/aves/bovinos/leche/compras_granos, así que comparte el riesgo de bloqueo por IP.

Formato de la respuesta. En un día hábil devuelve `{"posts": [...]}` con una fila por
(posición, ventana de embarque):

    {"fecha":"2026-09-10 00:00:00.000", "circular":"2056", "posicion":"12019000190C",
     "precio":434, "mesDesde":9, "añoDesde":2026, "mesHasta":9, "añoHasta":2026}

Sábados, domingos y feriados devuelven una LISTA VACÍA (`[]`), no un objeto: `resp.json()` es
`list`, no `dict`. Pedirle `.get("posts")` revienta con AttributeError. Es la forma normal de
decir "ese día no hubo cotización" y no es una falla — hay que distinguirla de una caída.

El `precio` es USD por tonelada FOB. La `circular` es la resolución que fijó ese precio: en los
90 una sola circular cubría el año entero (la 62 rige todo 1993), hoy cambian casi a diario.
"""
from __future__ import annotations

import datetime as dt
import time

from etl.core import http
from . import config

BASE = ("https://www.magyp.gob.ar/sitio/areas/ss_mercados_agropecuarios/ws/ssma/"
        "precios_fob.php?Fecha=")


class DiaSinCotizacion(Exception):
    """La fuente respondió bien pero sin datos: fin de semana, feriado o día sin circular."""


def url(fecha: dt.date) -> str:
    return BASE + fecha.strftime("%d/%m/%Y")


def _primer_dia(anio, mes) -> dt.date | None:
    """La ventana de embarque viene como (mes, año) y se guarda como primer día del mes."""
    try:
        return dt.date(int(anio), int(mes), 1)
    except (TypeError, ValueError):
        return None


def parse(payload, fecha: dt.date) -> list[dict]:
    """Filas de los cuatro granos del TCR para un día. Levanta DiaSinCotizacion si no hay nada.

    Se filtra por posición arancelaria exacta: la API devuelve ~40 posiciones por día y varias
    comparten NCM con distinta presentación (granel vs embolsado), que cotizan distinto.
    """
    posts = payload.get("posts", []) if isinstance(payload, dict) else []
    if not posts:
        raise DiaSinCotizacion(f"{fecha}: la fuente no publicó precios")

    filas = []
    for p in posts:
        producto = config.POSICIONES.get(p.get("posicion"))
        if producto is None:
            continue
        desde = _primer_dia(p.get("añoDesde"), p.get("mesDesde"))
        hasta = _primer_dia(p.get("añoHasta"), p.get("mesHasta"))
        precio = p.get("precio")
        if desde is None or hasta is None or precio is None:
            continue
        filas.append({
            "producto": producto,
            "date": fecha,
            "embarque_desde": desde,
            "embarque_hasta": hasta,
            "valor": float(precio),
            "posicion": p["posicion"],
            "circular": str(p.get("circular")) if p.get("circular") is not None else None,
        })
    if not filas:
        raise DiaSinCotizacion(f"{fecha}: ninguna de las cuatro posiciones del TCR cotizó")
    return filas


# Reintentos propios ante un 200 con cuerpo vacío (ver get_dia).
REINTENTOS_CUERPO_VACIO = 2
ESPERA_CUERPO_VACIO = 5.0


def get_dia(fecha: dt.date) -> tuple[list[dict], str]:
    """Baja y parsea un día. Devuelve (filas, url). Levanta DiaSinCotizacion si no hay datos.

    Reintenta cuando el cuerpo no es JSON. `etl.core.http` no cubre este caso: la fuente
    devuelve **200 con el cuerpo vacío** de vez en cuando, así que no hay status reintentable
    que mirar y `raise_for_status()` deja pasar la respuesta rota. Visto el 2026-09-11 con
    13/11/2007, que al pedirlo de nuevo trajo sus 145 filas sin problema. Sin este reintento un
    backfill de ~8.600 días se llena de agujeros intermitentes que sólo se ven al comparar.
    """
    u = url(fecha)
    for intento in range(REINTENTOS_CUERPO_VACIO + 1):
        # verify=False: el cert de magyp.gob.ar falla como en el resto de los ETLs del repo.
        resp = http.fetch(u, verify=False)
        try:
            payload = resp.json()
        except ValueError as e:
            if intento == REINTENTOS_CUERPO_VACIO:
                cuerpo = (resp.text or "")[:80]
                raise RuntimeError(
                    f"{fecha}: la respuesta no es JSON tras {intento + 1} intentos "
                    f"({e}); cuerpo: {cuerpo!r}") from e
            time.sleep(ESPERA_CUERPO_VACIO)
            continue
        return parse(payload, fecha), u
    raise RuntimeError(f"inalcanzable: {u}")  # el loop sale por return o raise


def marcar_sin_dato(conn, fecha: dt.date) -> None:
    """Deja registrado que la fuente contestó sin datos ese día. Idempotente.

    Una fecha futura NO se registra nunca, aunque la API conteste vacío: obviamente va a venir
    vacía, y anotarla la dejaría salteada para siempre en `load-history --solo-faltantes` cuando
    ese día llegue de verdad. El envenenamiento sería silencioso: el día nunca se pediría otra
    vez y nadie vería un error.
    """
    if fecha >= dt.date.today():
        return
    with conn.cursor() as cur:
        cur.execute(f"insert into {config.SIN_DATO_TABLE} (date) values (%s) "
                    f"on conflict (date) do nothing", (fecha,))
    conn.commit()


def dias_habiles(desde: dt.date, hasta: dt.date):
    """Lunes a viernes entre ambas fechas, inclusive. Los feriados los descarta la fuente."""
    d = desde
    while d <= hasta:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)
