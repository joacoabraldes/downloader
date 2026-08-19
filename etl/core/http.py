"""GET con reintentos y backoff contra las fuentes del Estado.

Por qué existe: los sitios .gob.ar fallan de DOS formas distintas y las dos son transitorias.

 1. Cortan por volumen y devuelven 403/429/5xx. En el backfill de `compras_granos`, tras ~700
    páginas seguidas el sitio empezó a devolver 403 en TODO, índices incluidos, y siguió
    bloqueando un buen rato.
 2. Directamente no completan el handshake y el request muere por timeout de conexión.

El caso 2 es el que se comía las corridas: la excepción se levanta ANTES de que exista una
`Response`, así que un reintento que sólo mira `status_code` ni se entera. El 2026-08-18 la
corrida de `granos` murió por eso (ConnectTimeout de 60s contra www.magyp.gob.ar) mientras
`compras_granos`, contra el MISMO host y dos horas antes, bajaba sus páginas sin problema.

El 404 NO se reintenta a propósito: es una página que no existe, no un corte. La distinción
importa aguas abajo — `compras_granos.load_history` usa el 404 para saltear semanas que el
índice linkea pero la fuente nunca publicó, y `automotriz.download_pdf` para saber que el
informe del mes todavía no salió.

En el último intento la falla se propaga tal cual (`ConnectTimeout`, `HTTPError`, lo que sea):
la corrida tiene que seguir fallando y mandando el mail cuando la fuente está realmente caída.

QUÉ NO PASA POR ACÁ, a propósito:

  - Los **probes HEAD** que preguntan "¿existe esta URL?" y tienen un camino alternativo cuando
    la respuesta es no (`automotriz.prensa_filename`, `leche._resolve_url`, `bovinos._resolver_xls`).
    Reintentar adentro de un barrido multiplica el peor caso por el largo del barrido:
    `automotriz.find_prensa` mira hasta 60 ids, y a 4 intentos x 90 s de timeout un caído de la
    fuente pasaría de ~2 minutos a ~6,6 horas. El barrido ya tolera que un id no conteste.
  - `patentamientos`, que habla con la API de mail.tm y ya tiene su propio loop de polling con
    tolerancia a fallas.

El default de `verify` es True a propósito, aunque casi todas las fuentes .gob.ar necesiten
False: si alguien agrega un call site y se olvida del parámetro, el error es quedarse corto de
permisivo, no aflojar TLS sin querer.
"""
from __future__ import annotations

import time

import requests

# Códigos que se reintentan: throttling y errores de servidor. El 403 entra acá porque es lo que
# devuelven estos sitios cuando cortan por volumen, no un problema de permisos.
REINTENTABLES = frozenset({403, 429, 500, 502, 503, 504})
REINTENTOS = 4
ESPERA_BASE = 5.0  # segundos; se duplica en cada intento (5, 10, 20)

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ConnectTimeout hereda de las dos, y ReadTimeout de Timeout: entre ambas queda cubierto todo
# el fallo de red que no llega a producir una Response.
ERRORES_DE_RED = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)


def fetch(
    url: str,
    *,
    timeout: int = 60,
    headers: dict | None = None,
    verify: bool = True,  # las fuentes con cert roto pasan verify=False explícito
    reintentos: int = REINTENTOS,
    espera_base: float = ESPERA_BASE,
    reintentables: frozenset = REINTENTABLES,
) -> requests.Response:
    """Baja `url` reintentando ante corte transitorio. Devuelve la Response ya validada.

    El encoding queda a cargo del caller: las páginas HTML de gov.ar declaran ISO-8859-1 y
    mandan Windows-1252 (`resp.encoding = resp.apparent_encoding`), pero por acá también pasan
    PDFs y planillas donde eso no aplica.
    """
    espera = espera_base
    for intento in range(reintentos):
        ultimo = intento == reintentos - 1
        try:
            resp = requests.get(url, timeout=timeout, verify=verify,
                                headers=headers if headers is not None else HEADERS)
        except ERRORES_DE_RED:
            if ultimo:
                raise
            time.sleep(espera)
            espera *= 2
            continue
        if resp.status_code in reintentables and not ultimo:
            time.sleep(espera)
            espera *= 2
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"inalcanzable: {url}")  # el loop siempre sale por return o raise
