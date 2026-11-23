# Hálito — el calor y el aire de Bucaramanga

Diseño técnico · v0.6 · 22-nov-2026

> Hálito: aliento, vapor que se exhala. El proyecto es una herramienta de **análisis de episodios** de calor y
> contaminación sobre un mapa 3D del área metropolitana (Bucaramanga, Floridablanca, Girón,
> Piedecuesta y Lebrija), inspirada en interfaces de análisis forense tipo «línea de tiempo + mapa
> + capas», no en un dashboard de tarjetas.

---

## 1. Objetivo

Responder preguntas como:

- ¿Qué zonas del área metropolitana se calientan más, y cuánto influye el relieve?
- ¿Qué pasó durante el episodio de PM2.5 de una fecha concreta: dónde empezó, cuánto duró, qué hacía el viento?
- ¿Cómo se comparan las estaciones entre sí una vez se corrige la diferencia de altitud?

**Qué demuestra en el portafolio:** ingesta incremental desde APIs públicas, limpieza de datos
con problemas reales (documentados abajo), modelado de series de tiempo y datos geoespaciales,
procesamiento de imágenes satelitales, detección automática de eventos, una API pensada para
el front y una interfaz no convencional.

**Fuera de alcance:** pronóstico. El clima del IDEAM llega casi al día; el aire de la CDMB solo
hasta diciembre de 2025, así que el análisis de contaminación es histórico.

---

## 2. Fuentes de datos (verificadas el 16-nov-2026)

### Clima — IDEAM (al día, con uno o dos días de retraso)

Un dataset nacional por variable, en formato largo. Se filtra **siempre por código de estación**.

| Variable | Dataset | Frecuencia |
|---|---|---|
| Temperatura del aire a 2 m | `sbwg-7ju4` | horaria (Palonegro, cada minuto) |
| Humedad relativa | `uext-mhny` | horaria |
| Presión atmosférica (hPa) | `62tk-nxj5` | horaria |
| Velocidad del viento | `sgfv-3yp8` | cada 10 min |
| Dirección del viento | `kiw7-v9ta` | cada 10 min |
| Precipitación | `s54a-sgyg` | cada 10 min |

Estaciones: Universidad Industrial de Santander (`0023195040`), Floridablanca (`2319500043`),
Llano Grande (`0023195110`) y El Pantano (`0023190600`) en Girón, Granja Piedecuesta
(`2319500065`, antes `0023190700`), Aeropuerto Palonegro (`0023195502`) y El Naranjo
(`0023190440`) en Lebrija, y Palogordo (`0023197690`, solo lluvia).

### Aire — CDMB (2024–2025)

Un dataset por estación, horario, con una columna por variable: PM2.5, PM10, O₃, NO, NO₂, SO₂, CO,
temperatura, humedad, presión (mmHg), viento, radiación y lluvia. Dos años completos
(1-ene-2024 → 1-ene-2026), con los decimales correctos.

| Estación | Dataset |
|---|---|
| Club Unión (Bucaramanga) | `5dsf-x7tb` |
| Colegio Gaitán (Bucaramanga) | `fvvs-hxtq` |
| Hospital del Norte (Bucaramanga) | `3v9a-6acv` |
| Centro Cultural (Piedecuesta) | `kh7q-whyx` |

El dataset nacional `g4t8-zkc3` cubre las mismas estaciones desde dic-2021, pero con el separador
decimal perdido hasta 2023 (§3.1). Queda como **histórico opcional**; no bloquea el proyecto.

### Otras fuentes

| Fuente | Uso |
|---|---|
| Landsat 8/9 C2 L2 (Planetary Computer, `landsat-c2-l2`) | Temperatura de superficie, 30 m |
| Tiles Terrarium (AWS Open Data) | Relieve 3D |
| OpenFreeMap (teselas vectoriales OpenMapTiles) | Mapa base, con estilo propio |
| Geoportal de la Alcaldía de Bucaramanga | Barrios y comunas (formato y licencia por confirmar) |

Descartadas por antiguas: Reporte Calidad de Aire AMB (`58ct-h586`, 2020) y Estaciones
hidrometeorológicas CDMB (`57ur-5p28`, 2022). Candidatas a futuro: red ciudadana RACiMo de la UIS
(`revr-udxt`, sin verificar) y eventos por desastres de Santander (`a4bc-a9tq`).

---

## 3. Problemas de calidad encontrados (y cómo se resuelven)

Esto es lo que hace interesante el pipeline. Hallazgos reales al consultar la API:

### 3.1 La CDMB perdió el separador decimal entre 2021 y 2023

En la estación Club Unión, la temperatura (`TAire10`) trae valores como `1932`, `196`, `1991`,
`197`. Son 19,32 °C, 19,6 °C, 19,91 °C y 19,7 °C: se borró el punto y, además, se recortaron los
ceros finales, así que **la cantidad de decimales varía fila a fila**. En 2023, 8.584 de 8.688
lecturas de temperatura están afectadas; desde 2024 la variable pasa a `TAire2` y llega bien.

Afecta también a humedad, presión, viento, radiación y contaminantes (máximos de PM2.5 de 2.413,
NO₂ de 6.318, presión de 68.339 mmHg). En contaminantes es **ambiguo**: un `59` de PM2.5 puede ser
59 o 5,9 µg/m³.

**Estrategia de reparación** (paso `clean_cdmb_decimals`):

1. Rango físico plausible por variable (p. ej. temperatura 10–40 °C, HR 0–100 %, PM2.5 0–500).
2. Para cada valor, candidatos `v / 10^k` con k = 0…3 que caen en el rango.
3. Si hay un solo candidato, se toma. Si hay varios, se elige el que minimiza el salto respecto a
   la mediana móvil de las horas vecinas ya resueltas (continuidad temporal).
4. Restricciones cruzadas: PM2.5 ≤ PM10 en la misma hora; la temperatura CDMB debe ser coherente
   con la estación IDEAM más cercana (corregida por altitud).
5. Cada valor guarda `quality_flag`: `ok`, `repaired_unique`, `repaired_inferred`, `rejected`.
   La UI puede ocultar o señalar lo reparado.
6. Validación: comparar la distribución reparada de 2022–2023 con la de 2024–2025 (sin el
   problema) por estación, mes y hora del día. Se publica como **reporte de calidad**.

### 3.2 Los nombres del IDEAM cambian; los códigos no siempre

- En agosto de 2026 el IDEAM pasó de `BUCARAMANGA` a `Bucaramanga` y añadió el código al nombre
  de la estación. Filtrar por texto pierde todo lo posterior (así se subestimó al principio la
  frescura de los datos). **Regla:** filtrar por `codigoestacion`.
- Piedecuesta Granja cambió de código (`0023190700` → `2319500065`). La tabla `station_alias`
  une las dos series.
- La altitud de la UIS figura como 118 m en el catálogo del IDEAM (está cerca de los 1.000 m).
  Se deja vacía hasta tomarla del modelo de elevación.
- Hay huecos reales: todas las estaciones del 25-jul al 12-ago de 2026; la UIS, del 4-jul al 30-sep.
- **Dos sensores para la misma variable.** Palonegro reporta cada variable con el sensor
  convencional (p. ej. `0240`, lluvia cada 10 min) y con uno GPRS (`0257`, cada minuto). Sumarlos
  duplicaba la lluvia (unos 3.000 mm al año frente a ~1.500 de sus vecinas). En cada hora se usa un
  solo sensor: el de código menor, que es el convencional.
- **Acumulados de lluvia que se solapan.** En las horas en que solo reporta el sensor GPRS de
  Palonegro, cada lectura de minuto es un acumulado que se pisa con las demás: sumarlas daba
  ~20 mm por hora lluviosa frente a ~2,4 normales. La lluvia con más de seis lecturas por hora se
  rechaza (107 horas). Con las tres correcciones Palonegro queda en ~1.550 mm al año.
- **Lecturas repetidas.** En algunos periodos el portal publica cada lectura dos veces (horas con
  doce lecturas de 10 minutos). Se deja una por instante y sensor antes de agregar.
- **El portal es lento e inestable con consultas grandes.** Un tramo de 120 días de lluvia puede
  tardar más de 3 minutos o colgarse; uno de 30 días responde en unos 6 segundos. Se piden tramos
  de 30 días, con 2 minutos de espera máxima por consulta y reintentos. Si una estación falla, su
  corrida queda como fallida, las demás siguen y la próxima ingesta la reintenta.
- **El Naranjo (Lebrija) registra unos 4.000 mm al año**, el doble que cualquier otra estación.
  No se encontró una causa en los datos (un solo sensor, sin lecturas repetidas); queda señalado
  como dato por verificar.

### 3.3 El IDEAM cambió de zona horaria sin anunciarlo

Hasta julio de 2026 el IDEAM publica en **UTC**; al reanudar el 12 de agosto de 2026 pasó a
**hora de Colombia**. No está documentado: se detectó porque la hora más caliente del día salía
a las 17–18 h en lugar de las 12–13 h, y saltaba justo en esa fecha, en todas las estaciones.
La ingesta resta cinco horas a todo lo anterior al 1 de agosto de 2026. La CDMB siempre publica
en hora local.

### 3.4 Otros

- **Faltantes de la CDMB:** `NA` significa que la estación no mide la variable (no genera fila);
  `-999` es una lectura inválida (se guarda como `rejected`, sin valor y con el crudo).
- **Unidades:** la presión se unifica en hPa (la CDMB la publica en mmHg).
- **Resolución mixta:** minuto, 10 minutos u hora según estación y variable. Todo se lleva a
  horario: media, suma para la lluvia y media circular para la dirección del viento. Se conserva
  el número de lecturas de cada hora.
- **Almacenamiento:** todo se guarda como `timestamptz`.
- **Consultas lentas:** los datasets nacionales tienen decenas de millones de filas, y pedirlas
  ordenadas multiplica el tiempo de respuesta (minutos frente a segundos). El cliente pide todo de
  una vez y sin ordenar, filtrando por igualdad de código y por fecha; solo si no cabe en una
  página vuelve a pedir con un orden total (`…, :id`). Las estaciones se consultan en paralelo.
- **Huecos:** no se interpolan más de 2 horas seguidas.

---

## 4. Arquitectura

```
 datos.gov.co (Socrata)      Planetary Computer (STAC)
        │                            │
        ▼                            ▼
 ┌─────────────────────────────────────────────┐
 │  Ingesta y procesamiento (Python + Dagster)  │
 │  extract → raw → clean → hourly → episodes   │
 │  landsat → LST → anomalía → H3               │
 └──────────────────────┬──────────────────────┘
                        ▼
        PostgreSQL 16 + PostGIS + TimescaleDB
                        │
                        ▼
              API (FastAPI, solo lectura)
                        │
                        ▼
   Web (Vue 3 + MapLibre GL + deck.gl + uPlot)
```

### Stack

| Capa | Elección | Por qué |
|---|---|---|
| Ingesta | Python 3.12, `httpx`, `polars`, `pydantic` | Polars es rápido con millones de filas y su API es clara |
| Orquestación | Comandos `halito` por ahora; **Dagster** (assets) cuando haya más de un pipeline | Los pasos ya son idempotentes y por estación, así que envolverlos en assets es directo |
| Raster | `pystac-client`, `planetary-computer`, `rasterio`, `numpy`, `h3` | Estándar para Landsat; se lee solo la ventana necesaria de cada imagen (COG) |
| Calidad | Rangos válidos en la tabla `variable` + `pytest` | Una sola fuente de verdad para los límites físicos |
| Base de datos | PostgreSQL 16 + PostGIS + TimescaleDB (imagen `timescale/timescaledb-ha`) | Series de tiempo con agregados continuos y geometría en un solo motor |
| API | FastAPI + SQLAlchemy 2 (async) + `asyncpg` | Tipado, OpenAPI automático |
| Front | Vue 3 + Vite + TypeScript | Lo dominas; el foco del proyecto está en los datos |
| Mapa | MapLibre GL JS 5 (relieve 3D, cielo, niebla) + `h3-js` | 3D sin claves de pago. Las estaciones son marcadores nativos y los hexágonos de calor una capa de relleno nativa: ambos se pliegan solos sobre el relieve, cosa que deck.gl no hace. MapLibre 6 se descartó por ahora: su worker no arranca bajo Vite |
| Gráficas | SVG propio | La línea de tiempo es la gráfica de la semana (168 puntos por estación); no hace falta una librería |
| Despliegue | Docker Compose en un servidor: Caddy (HTTPS automático) sirve la web y reenvía `/api` a la API | Un solo origen, sin CORS; ver [despliegue.md](despliegue.md) |

Se elige Python en lugar de PHP/Laravel para el backend a propósito: el proyecto apunta a un
perfil de data y ese ecosistema (Polars, Dagster, rasterio) es el que se espera ver.

### Estructura del repositorio

```
halito/
├── backend/                 # un solo proyecto Python (uv)
│   ├── src/halito/
│   │   ├── db/              # modelos, migraciones (Alembic)
│   │   ├── ingest/          # catálogo, cliente Socrata, IDEAM, CDMB, carga
│   │   ├── quality/         # limpieza y agregación horaria
│   │   └── api/             # FastAPI
│   └── tests/
├── web/                     # Vue 3
├── docker-compose.yml
└── docs/
```

---

## 5. Modelo de datos

```sql
-- Catálogo
station (
  id serial PK, source text,            -- 'ideam' | 'cdmb'
  source_code text, name text, municipality text,
  geom geography(Point), elevation_m int,
  active_from date, active_to date,
  UNIQUE (source, source_code)
)

variable (
  code text PK,                          -- 'temp_2m', 'temp_10m', 'rh', 'pm25', 'o3', 'wind_speed', 'wind_dir'...
  name text, unit text, kind text,       -- 'meteo' | 'pollutant'
  valid_min numeric, valid_max numeric
)

source_variable_map (source, source_code, variable_code)   -- 'TAire10' → 'temp_10m'

-- Hechos (hypertable TimescaleDB, partición por mes)
observation (
  station_id int, variable_code text, ts timestamptz,
  value double precision, raw_value text,
  quality_flag text, n_samples smallint,
  ingestion_run_id int,
  PRIMARY KEY (station_id, variable_code, ts)
)

-- Agregados continuos
observation_daily  (station_id, variable_code, day, mean, min, max, n)   -- sin valores rechazados
-- Percentiles y medias móviles de 8 h / 24 h (normas de O₃ y PM) no se pueden materializar
-- como agregado continuo: se calculan en la detección de episodios.

-- Satélite
lst_scene (id, landsat_id, acquired_at, cloud_pct, clear_pct, season, used bool)
lst_scene_cell (scene_id, h3, lst_c, anomaly_c)      -- lo que vio cada escena en cada celda
lst_cell  (h3 text, period text,        -- 'all', 'DEF', 'MAM', 'JJA', 'SON'
           lst_c_median real, anomaly_c real, n_scenes smallint, PRIMARY KEY (h3, period))

-- Eventos
episode (
  id serial PK, kind text,               -- 'heat' | 'pm25' | 'o3' | 'calm' | 'heavy_rain'
  starts_at timestamptz, ends_at timestamptz,
  peak_value real, peak_station_id int, peak_at timestamptz,
  stations int[], severity text, summary text
)

-- Operación
ingestion_run (id, source, dataset_id, watermark timestamptz, rows_in, rows_ok,
               rows_repaired, rows_rejected, started_at, finished_at, status)
```

Volumen esperado: unos pocos millones de observaciones (11 estaciones × ~15 variables × 4 años
horarios) y unas 3.000 celdas H3 de resolución 9 (~0,1 km²) para el área urbana. Cabe con holgura
en un VPS pequeño.

---

## 6. Pipelines

### 6.1 Estaciones (diario)

1. `socrata_extract`: por dataset y estación, pide filas con `ts > watermark` paginando con
   `$limit/$offset` y `$order` estable. Guarda el JSON crudo particionado por fecha (reprocesable).
2. `normalize`: formato largo común (`station, variable, ts, raw_value`), mapeo de códigos y zona horaria.
3. `clean`: rangos, reparación de decimales (§3.1), duplicados, agregación a horario.
4. `load`: `INSERT … ON CONFLICT DO UPDATE` (idempotente) y registro en `ingestion_run`.
5. `quality_report`: porcentaje de valores ok / reparados / rechazados por estación y mes.

### 6.2 Temperatura de superficie (`make heat`)

1. Buscar en Planetary Computer las escenas Landsat 8/9 C2 L2 que tocan el área (trayectorias
   7 y 8, fila 55), con nubosidad de escena menor al 80 %.
2. Leer solo la ventana del área de estudio de las bandas `lwir11` (ST_B10) y `qa_pixel`,
   remuestreadas a una rejilla fija de ~30 m para que todas las escenas compartan píxeles.
3. Enmascarar con `QA_PIXEL` (relleno, nube, nube dilatada, cirros y sombra) y convertir a °C:
   `DN × 0.00341802 + 149.0 − 273.15`.
4. Promediar por celda H3 de resolución 9 (~0,1 km²). Una celda cuenta si al menos la mitad de
   sus píxeles está despejada; una escena, si al menos el 40 % de sus celdas es válido.
5. **Anomalía** = temperatura de la celda − mediana de la escena. Es comparable entre fechas (el
   valor absoluto depende del día) y es lo que muestra la isla de calor.
6. Guardar lo que vio cada escena en cada celda (`lst_scene_cell`): así las corridas siguientes
   solo procesan escenas nuevas.
7. Publicar en `lst_cell` la mediana por celda, para todo el periodo y por trimestre climático
   (DEF, MAM, JJA, SON), exigiendo al menos 3 escenas.

Primera corrida (2023 → nov-2026): 249 escenas, 140 utilizables, 108 descartadas por nubes y
1 sin banda térmica. Resultado: 8.566 celdas, cada una con entre 10 y 135 imágenes. El 90 % de
las anomalías cae entre −9 °C (montaña) y +6 °C (zonas urbanas).

Limitaciones que la interfaz declara: es la temperatura del suelo y los techos hacia las
10:30 a. m. (hora de paso del satélite), **no** la del aire; y como la montaña es más fría por
altitud, parte del contraste es relieve y no urbanización.

### 6.3 Detección de episodios (`halito detect-episodes`)

Cada regla produce **tramos por estación** (horas o días seguidos por encima del umbral). Los
tramos de distintas estaciones que se solapan, o casi, se agrupan en un **episodio**, con el pico
más alto como referencia. La detección se recalcula completa tras cada ingesta (`make ingest`).

| Tipo | Regla | Une tramos con huecos de | Une estaciones separadas por |
|---|---|---|---|
| Calor | Máxima diaria (≥ 18 h medidas) por encima del percentil 90 de la estación para ese mes, 3 días seguidos | 0 | 0 días |
| PM2.5 | Media móvil de 24 h (≥ 18 h medidas) por encima de 37 µg/m³ | 2 h | 6 h |
| Ozono | Media móvil de 8 h (≥ 6 h medidas) por encima de 100 µg/m³ | 2 h | 6 h |
| Aguacero | Más de 20 mm en una hora | 1 h | 3 h |

- Los umbrales de PM2.5 y ozono son las normas de 24 h y 8 h de la Resolución 2254 de 2017.
  Hay que confirmar que siguen vigentes antes de publicar.
- El percentil del calor se calcula con los años disponibles (desde 2024), así que «habitual»
  significa «habitual en estos años», no una climatología de 30 años.
- Para PM2.5 y ozono se cuentan las **horas de calma** del episodio (viento medio entre estaciones
  menor a 1 m/s): la calma suele explicar la acumulación de contaminantes.
- La calma no es un episodio propio: en Bucaramanga casi todas las noches son calmas.
- Severidad: PM2.5 según el índice de calidad del aire («Dañina para grupos sensibles» hasta
  55,4 µg/m³, «Dañina» desde 55,5); aguacero «Fuerte» o «Muy fuerte» (≥ 40 mm/h); calor «Corta»
  o «Larga» (≥ 5 días).
- Resultado con los datos de 2024 → nov-2026: **148 episodios** (24 de calor, 4 de PM2.5 en
  marzo y abril de 2024, 2 de ozono y 118 aguaceros, 31 de ellos de 40 mm/h o más).
- El resumen de cada episodio se arma con plantillas y sus números («El PM2.5 superó la norma de
  24 horas durante 18 horas. La media de 24 horas llegó a 38,8 µg/m³ en Centro Cultural…»).

**Pendiente:** para comparar estaciones a distinta altitud, ajustar la temperatura a una altitud
de referencia con el gradiente estándar (−6,5 °C/km), como opción que nunca reemplace al dato
medido. No está construido.

---

## 7. API

Solo lectura. Las respuestas de series usan **formato columnar** para que el front las pase
directo al mapa y a la línea de tiempo sin transformar.

| Método y ruta | Uso |
|---|---|
| `GET /stations` | Estaciones con ubicación, altitud, variables y rango de fechas |
| `GET /stations/{id}/series?vars=temp_2m,pm25&from&to&res=hour\|day` | Serie de una estación |
| `GET /frames?var=pm25&from&to&step=1h` | Matriz estaciones × tiempo para animar la línea de tiempo |
| `GET /heat` | Escenas usadas, fechas y periodos disponibles |
| `GET /heat/cells?period=all\|DEF\|MAM\|JJA\|SON` | Celdas H3 en columnas: temperatura de superficie, anomalía y número de escenas |
| `GET /transect?line=<geojson>&period=…` | Perfil de elevación + LST a lo largo de una línea dibujada |
| `GET /episodes?kind&start&end` | Episodios, del más reciente al más antiguo, con resumen, pico, estaciones y horas de calma |
| `GET /quality` | Reporte de calidad de datos |

Ejemplo de `/frames`:

```json
{
  "variable": "pm25", "unit": "µg/m³",
  "stations": [12, 13, 14, 15],
  "timestamps": ["2025-02-10T00:00:00-05:00", "…"],
  "values": [[18.2, 22.1, null, 15.0], "…"],
  "flags":  [["ok", "repaired_inferred", null, "ok"], "…"]
}
```

Una semana a resolución horaria son 168 × 11 valores: el front la carga entera y la animación
corre sin más peticiones. Las respuestas de periodos cerrados se sirven con
`Cache-Control: immutable`.

---

## 8. Interfaz

Distribución tomada de la referencia (análisis forense con mapa 3D), adaptada:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Hálito    │ Episodio: PM2.5 10–13 feb 2025 ▾ │ 📅 rango │ Mapa · Estaciones · Episodios │ Compartir │
├───────────────────────────────────────────────────────┬──────────────────┤
│                                                       │ Capas │ Análisis │ Episodio │
│                                                       │ ☑ Relieve 3D        ───● │
│         MAPA 3D DEL ÁREA METROPOLITANA                 │ ☑ Anomalía térmica   ──●  │
│   meseta · escarpe · valle de Girón · Piedecuesta      │ ☐ Temp. superficie        │
│   hexágonos de calor + estaciones como columnas        │ ☑ Estaciones (temp.)      │
│   vectores de viento animados                          │ ☑ PM2.5 (ICA)             │
│                                                       │ ☐ Ozono · ☑ Viento        │
│                                       2D │ 3D │ ⛶     │ ☐ Comunas                 │
│                                                       ├──────────────────┤
│                                                       │ Condiciones en el punto   │
│                                                       │ 27,4 °C · HR 64 % · 1,2 m/s SO │
│                                                       │ PM2.5 41 µg/m³ · Dañina sensibles │
├───────────────────────────────────────────────────────┴──────────────────┤
│ ▶ ❚❚ ⏭ 1× │──●────────────▲───────▲────────────│ Eventos clave            │
│           10 feb        11 feb       12 feb      │ ● Inicio calma   10 feb 22:00 │
├──────────────┬──────────────┬────────────────────┤ ▲ PM2.5 > 37     11 feb 06:00 │
│ Temperatura  │ PM2.5 vs norma│ Perfil del transecto │ ▲ Pico 58 µg/m³  11 feb 08:00 │
└──────────────┴──────────────┴────────────────────┴──────────────────────────┘
```

Interacciones principales:

- **Reproductor:** al mover la línea de tiempo cambian a la vez los colores de las estaciones, los
  vectores de viento y el cursor de las tres gráficas.
- **Clic en estación:** serie completa y comparación con la estación más parecida.
- **Clic en hexágono:** temperatura de superficie por temporada y barrio al que pertenece.
- **Transecto:** dibujas una línea (p. ej. del escarpe a Girón) y aparece el perfil de altura
  con la temperatura de superficie superpuesta.
- **URL con estado:** episodio, capas, cámara y hora quedan en la URL, para compartir una vista exacta.
- **Datos reparados:** se dibujan con trazo punteado y una nota explica el porqué.

Dirección visual: tonos tierra y cálidos para el calor (rampa secuencial), escala ICA oficial para
el aire y el relieve como protagonista con iluminación y niebla atmosférica. Modo claro y oscuro.

---

## 9. Fases

| Fase | Entregable | Hito visible |
|---|---|---|
| 0. Base | Monorepo, Docker Compose, esquema y migraciones, CI con lint y tests | `docker compose up` levanta la BD vacía |
| 1. Estaciones | Ingesta incremental IDEAM + CDMB, limpieza y reporte de calidad | `make ingest` deja la base al día y `make report` muestra la cobertura |
| 2. MVP visual | API de estaciones y frames, mapa 3D con estaciones y línea de tiempo | Reproducir una semana cualquiera sobre el relieve |
| 3. Calor satelital | Pipeline Landsat → H3, capa de anomalía, detalle por celda | Mapa de islas de calor por temporada |
| 4. Episodios | Detección automática, panel de episodio, eventos clave, transecto | Abrir un episodio y que la interfaz lo cuente sola |
| 5. Publicación | Imágenes Docker, compose de producción, copia de datos, README como caso de estudio con capturas | Falta elegir servidor y dominio para el enlace público; el video queda opcional |

La fase 2 es la primera que se ve; conviene llegar a ella pronto y pulir después.

---

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Reparación de decimales ambigua (solo histórico 2021–2023) | Es opcional; marcas de calidad y validación contra 2024–2025 |
| Pocas estaciones | No se interpola el aire entre estaciones; se muestran puntos. El detalle espacial lo aporta el satélite |
| Nubosidad sobre Bucaramanga | Compuestos por temporada con varias escenas; mostrar `n_scenes` por celda |
| El aire llega solo hasta dic-2025 | El clima se muestra al día y el aire como análisis de episodios; la UI indica la fecha del último dato por fuente |
| Límites de la API de Socrata | App token, paginación, caché del crudo y reintentos con *backoff* |
| Cambios de esquema en la fuente | Tests que comparan el catálogo del código con el de la base; la ingesta registra cada corrida |

---

## 11. Decisiones

| Tema | Decisión |
|---|---|
| Barrios y comunas | Geoportal de la Alcaldía de Bucaramanga |
| Relieve | Tiles Terrarium (AWS Open Data) en la primera versión; tiles propios desde Copernicus DEM quedan como mejora |
| Motor 3D | MapLibre GL 5 para todo (relieve, cámara, estaciones y hexágonos). Sin deck.gl ni three.js por ahora |
| Nombre | **Hálito** (`halito` en repositorio, paquetes y dominio) |
