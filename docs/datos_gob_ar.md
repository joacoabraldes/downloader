# API Series de Tiempo del Estado (`apis.datos.gob.ar`)

Evaluación de la API oficial de series de tiempo de la Argentina como fuente para este repo.
Hecha en **agosto de 2026**, a partir de la skill `indec` (ver el final).

Las dos preguntas que se contestan acá:

1. ¿Sirve para **reemplazar** alguno de los scrapers de PDF/Excel que ya tenemos? → **No.**
2. ¿Qué series **nuevas** conviene sumar? → Nueve, listadas abajo.

## Qué es

`https://apis.datos.gob.ar/series/api` — API pública de la Dirección Nacional de Datos e
Información Pública. Sin API key, sin autenticación, con documentación oficial y política de
no romper compatibilidad desde 2017. Agrega series de INDEC, BCRA, Ministerio de Economía,
Secretaría de Trabajo y organismos provinciales.

Su índice de búsqueda reportaba **30.922 series** al momento de esta evaluación.

## 1. No reemplaza ningún scraper nuestro

Era la hipótesis atractiva —cambiar PDFs frágiles por una API oficial— y no se sostiene. El
detalle, dataset por dataset:

| Nuestro dataset | Serie más parecida en la API | Por qué no sirve como reemplazo |
|---|---|---|
| `acero` | `359.3_ACERO_CRUDUDO__11` (Cámara Argentina del Acero, 1965→) | Es la misma serie, pero **no incorpora las revisiones** de la Cámara (ver abajo) |
| `cemento` | `38.3_CEM_1994_M_7` | Otro concepto (ventas al mercado interno, no despacho nacional) y va un mes atrás del nuestro |
| `automotriz` | `41.3_AA_0_A_23` | Dos meses atrás del nuestro, y no es producción de ADEFA sino automotores del Ministerio de Economía |
| `bovinos` | `41.3_FCV_0_A_18` | Métrica distinta: faena en cabezas, no producción en toneladas res con hueso |
| `leche`, `aves`, `granos`, `demanda_energia` | `AGRO_*` / índices IPI | Las series físicas están **discontinuadas** (terminan entre 2016 y 2020); lo vigente son índices, no niveles |

### La evidencia del acero

Comparando `359.3_ACERO_CRUDUDO__11` contra `etl_acero_actual`, cinco de los seis últimos meses
coinciden exacto. Enero-2026 no:

```
mes         API      nuestro
2026-01   351.400   345.200   <-- difiere
2026-02   272.200   272.200
2026-03   387.400   387.400
2026-04   375.600   375.600
2026-05   399.400   399.400
2026-06   346.300   346.300
```

Ese `351.400` es **exactamente el valor que tiene nuestro `Acero.xlsx` histórico** (estado NULL)
para enero-2026. El PDF vigente de la Cámara dice `345.200`, y es el que nuestra vista `_actual`
prioriza porque entra como `definitivo`.

O sea: la Cámara revisó enero a la baja, **nuestro ETL tomó la revisión y la API no**. Migrar el
scraper a la API no sería un empate, sería perder revisiones.

> Conclusión operativa: los scrapers se quedan como están. Esta API es **complemento**, no
> reemplazo. Si alguien vuelve a proponer la migración, el contraejemplo es enero-2026.

## 2. Series nuevas que conviene sumar

Ninguna de estas está hoy en el repo. Todas son mensuales y están al día:

| Indicador | ID | Rango | Por qué encaja |
|---|---|---|---|
| ISAC construcción, nivel general | `33.2_ISAC_NIVELRAL_0_M_18_63` | 2012-01 → 2026-06 | Es el agregado natural de `cemento` |
| IPI manufacturero, serie original | `453.1_SERIE_ORIGNAL_0_0_14_46` | 2016-01 → 2026-06 | Agrega `acero` y `automotriz` |
| IPC nacional, nivel general | `148.3_INIVELNAL_DICI_M_26` | 2016-12 → 2026-06 | Deflactor para todo lo demás |
| Exportaciones totales (USD MM) | `74.3_IET_0_M_16` | 1992-01 → 2026-06 | Macro, 34 años de historia |
| Importaciones totales (USD MM) | `74.3_IIT_0_M_25` | 1992-01 → 2026-06 | Ídem |
| Ventas en supermercados | `455.1_VENTAS_TOTLOS_0_M_30_43` | 2017-01 → 2026-05 | Consumo efectivo, para contrastar con el `icc` |
| Ventas en centros de compras | `458.1_TOTALTAL_ABRI_M_5_38` | 2017-01 → 2026-05 | Ídem |
| RIPTE | `158.1_REPTE_0_0_5` | 1994-07 → 2026-05 | Salarios |
| Salario mínimo, vital y móvil | `57.1_SMVMM_0_M_34` | 1965-01 → 2026-08 | La serie más fresca del catálogo |

El par **supermercados + centros de compras** es el de mayor valor marginal: el `icc` mide lo que
la gente *dice* que va a consumir y estas dos miden lo que efectivamente consumió. Juntas valen
más que por separado.

> El EMAE nivel general existe (la familia `11.3_*` está al día a 2026-05) pero el buscador no
> devuelve el ID del nivel general con ninguna de las consultas probadas; hay que fijarlo a mano
> antes de usarlo. No lo dimos por identificado.

## 3. Forma del ETL

A diferencia del resto del repo —un scraper por fuente, porque cada fuente es un PDF o un Excel
distinto— acá es **una sola API, un solo parser, N series por configuración**. Un único dataset
con la lista de IDs en su `config.py` cubre las nueve de arriba, y sumar una décima no es escribir
código sino agregar una línea.

Es, por lejos, el ETL más barato que se puede agregar a este repo.

## 4. Trampas verificadas

Tres cosas que aparecieron al probarla y que hay que tener en cuenta al implementar:

- **Devuelve `403` con el User-Agent por defecto** de `urllib` y de `requests`. Hay que mandar uno
  explícito. Es la primera pared contra la que se choca cualquiera.
- **El buscador matchea solo título y descripción, nunca IDs**, y la relevancia es pobre: buscar
  "turistas internacionales" devuelve reservas del BCRA. Los IDs se fijan en config; no se
  descubren en runtime.
- **Hay basura en el catálogo, y la API la sirve.** `359.2_ACERO_CRUDUDO__11` no sólo declara un
  rango que va de **2065 a 2126**: al pedirle el último dato devuelve un valor fechado
  **2126-04**. No alcanza con confiar en los metadatos; hay que validar las fechas al parsear y
  descartar lo que caiga fuera de un rango razonable.

## 5. La skill `indec`

Vive en `.claude/skills/indec/`. Envuelve esta misma API y trae un catálogo de series conocidas,
documentación de endpoints y un CLI (`scripts/fetch_indec.py`).

Se instaló **a mano** (copiando los archivos del repo `gauss314/skills`): el instalador oficial
`npx skills add` exige Node ≥ 22.20 y el servidor corre Node 18.19. Actualizar el Node de la
máquina que ejecuta todos los ETL no se justificaba para esto.

Como no pasó por el instalador, **no hay lockfile ni marca de versión**. Queda anotado acá:

| | |
|---|---|
| Origen | `https://github.com/gauss314/skills`, subcarpeta `skills/indec` |
| Commit vendorizado | `52c9d96c7882` (2026-06-05) |
| Copiado el | 2026-08-12 |

Para actualizarla, re-copiar esa subcarpeta sobre `.claude/skills/indec/` y actualizar el commit
de arriba. Conviene volver a auditar el script si cambió.

El script se auditó antes de usarlo: importa solo la stdlib y `requests`, apunta a una única URL
(la API oficial) y escribe a disco únicamente cuando se le pasa `--output`. Sin `subprocess`,
`exec` ni `pickle`.

> La skill declara "~4250 series"; la API reportaba 30.922. El número de la skill quedó viejo.

---

Los **14 IDs citados en este documento se verificaron contra la API** el 2026-08-12: los catorce
resuelven y sus últimos datos son los que figuran en las tablas. Para revalidarlos:

```bash
curl -s -H 'User-Agent: Mozilla/5.0' \
  'https://apis.datos.gob.ar/series/api/series/?ids=33.2_ISAC_NIVELRAL_0_M_18_63&last=1&format=json'
```
