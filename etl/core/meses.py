"""Meses en español: todas las variantes que aparecen en las fuentes, en un solo lugar.

## Por qué existe

Hasta agosto de 2026 había **siete** mapas de meses repartidos por el repo —en `utdt`, `acero`,
`aves`, `cemento`, `granos`, `patentamientos` y `automotriz`— y cada uno cubría un conjunto de
variantes distinto. No era duplicación inofensiva: se estaban rompiendo en lugares diferentes.

    patentamientos:  "Sep.2026" OK   "Set.2026" OK   "Sept.2026" NO MATCHEA
    utdt:            "sep-26"   OK   "set-26"   NO MATCHEA   "sept-26"  OK
    granos:          sólo "SEPTIEMBRE" (sin "SETIEMBRE")

Y el modo de falla es el peor: **no explota, no matchea**. El mes simplemente no aparece, la
serie se queda quieta y nadie se entera hasta que alguien mira el dato a mano.

Septiembre es el mes problemático y por lejos: se escribe `septiembre`, `setiembre`, `sept`,
`sep` y `set` según la fuente. Todas esas formas están documentadas acá porque **se vieron de
verdad** en documentos del repo, no por completitud teórica.

## Cómo se usa

    from etl.core import meses

    meses.numero("Sept")        -> 9      (tolerante: mayúsculas, acentos, punto final)
    meses.numero("SETIEMBRE")   -> 9
    meses.numero("cualquiera")  -> None

    # Para armar un regex, SIEMPRE con este helper y nunca con "|".join(...) a mano:
    rx = re.compile(meses.alternancia() + r"\\.?\\s*(\\d{4})", re.IGNORECASE)

## La trampa del alternador (por esto existe `alternancia()`)

Un `"|".join(...)` sobre las claves respeta el orden del diccionario, y en un alternador de
regex **gana la primera rama que matchea, no la más larga**. Si `sep` va antes que `sept`, el
texto "Sept.2026" matchea `sep` y deja un `t` colgado que rompe lo que venga después: el regex
falla entero y el mes se pierde en silencio.

`utdt.py` ya había descubierto esto y lo dejó anotado ("'sept' va antes que 'sep' para que el
alternador no corte corto"), pero el aprendizaje quedó encerrado ahí y `patentamientos` volvió a
caer en el mismo pozo. `alternancia()` ordena de más larga a más corta, así que la regla queda
codificada una sola vez y no se puede reintroducir el bug por descuido.
"""
from __future__ import annotations

import re
import unicodedata

# Nombres completos y abreviaturas van SEPARADOS porque hay parsers que necesitan la distinción.
# `patentamientos`, por ejemplo, prueba primero el encabezado abreviado con año de 2 dígitos
# ('JUN.26') y recién después el título con nombre completo y año de 4. Si se mezclaran, un
# "diciembre 15" podría leerse como diciembre de 2015 al matchear el nombre largo contra el año
# corto. Cada caller elige la forma que su fuente realmente usa.
#
# Ojo: la longitud NO separa las dos familias ('mayo' es larga y tiene 4 letras, 'sept' es corta
# y también). Por eso son dos dicts explícitos y no un filtro por `len`.
LARGAS: dict[int, tuple[str, ...]] = {
    1:  ("enero",),
    2:  ("febrero",),
    3:  ("marzo",),
    4:  ("abril",),
    5:  ("mayo",),
    6:  ("junio",),
    7:  ("julio",),
    8:  ("agosto",),
    9:  ("septiembre", "setiembre"),
    10: ("octubre",),
    11: ("noviembre",),
    12: ("diciembre",),
}

CORTAS: dict[int, tuple[str, ...]] = {
    1:  ("ene",),
    2:  ("feb",),
    3:  ("mar",),
    4:  ("abr",),
    5:  ("may",),
    6:  ("jun",),
    7:  ("jul",),
    8:  ("ago",),
    # El mes con más formas en circulación. `sept` lo usa ADEFA en el encabezado del
    # comparativo; `set` aparece en planillas de SIOMAA. Faltar una de las tres es lo que
    # tenía rotos a `patentamientos` (sin `sept`) y a `utdt` (sin `set`).
    9:  ("sept", "sep", "set"),
    10: ("oct",),
    11: ("nov",),
    12: ("dic",),
}

# Unión, para el lookup y para los callers a los que les da igual la forma.
VARIANTES: dict[int, tuple[str, ...]] = {
    n: LARGAS[n] + CORTAS[n] for n in LARGAS
}

# Nombre canónico para escribir (no para parsear): `cemento` arma con esto la etiqueta que
# busca en la página, y `export` lo usa para rotular.
NOMBRE: dict[int, str] = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre",
    12: "Diciembre",
}

# variante normalizada -> número. Se arma una sola vez.
_INDICE: dict[str, int] = {v: n for n, vs in VARIANTES.items() for v in vs}


def normalizar(texto: str) -> str:
    """minúsculas, sin acentos, sin punto final y sin espacios de borde.

    Es lo que hace falta para que "Set.", "SEPTIEMBRE" y "setiembre" caigan todas en la misma
    clave. No colapsa espacios internos: acá siempre se busca una palabra sola.
    """
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return sin_acentos.strip().rstrip(".").strip().lower()


def numero(texto: str) -> int | None:
    """Número de mes (1-12) de un nombre o abreviatura. None si no se reconoce.

    Devuelve None en vez de levantar a propósito: el caller casi siempre está barriendo texto
    y necesita distinguir "esta línea no es un mes" de "esta línea es un mes que no entiendo".
    Cuando lo segundo importa —un encabezado que TIENE que traer un mes— el caller corta él.
    """
    return _INDICE.get(normalizar(texto))


_FORMAS = {"todas": VARIANTES, "largas": LARGAS, "cortas": CORTAS}


def alternancia(*, formas: str = "todas", grupo: bool = True) -> str:
    """Rama de regex con las variantes, **ordenadas de más larga a más corta**.

    Ese orden es el punto de la función: en un alternador gana la primera rama que matchea, no
    la más larga, así que con `sep|sept` el texto "Sept.2026" matchea `sep`, deja un `t` colgado
    y el regex falla entero — el mes se pierde sin que nadie se entere. Ordenando por longitud
    el problema desaparece y no se puede reintroducir por descuido.

    `formas`: 'todas' | 'largas' (nombres completos) | 'cortas' (abreviaturas).
    `grupo=True` (default) devuelve un grupo de captura, listo para `m.group(1)`.
    """
    try:
        fuente = _FORMAS[formas]
    except KeyError:
        raise ValueError(f"formas={formas!r}; esperaba una de {sorted(_FORMAS)}") from None
    todas = sorted({v for vs in fuente.values() for v in vs}, key=len, reverse=True)
    cuerpo = "|".join(re.escape(v) for v in todas)
    return f"({cuerpo})" if grupo else f"(?:{cuerpo})"


def anio_2d(anio: int, *, pivote: int = 70) -> int:
    """Expande un año de 2 dígitos ("26" -> 2026). Deja intactos los de 4.

    El pivote sobra para todas las series del repo (la más vieja arranca en 1994), pero se deja
    explícito para que no haya que adivinar qué pasa con "98".
    """
    if anio >= 100:
        return anio
    return anio + 2000 if anio < pivote else anio + 1900
