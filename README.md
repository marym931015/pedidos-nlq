# Pedidos NLQ — consultas en lenguaje natural sobre datos de pedidos

**Repositorio**: https://github.com/marym931015/pedidos-nlq

Pipeline completo sobre un CSV de pedidos sucio: generación de datos con errores
controlados, limpieza con informe de calidad, carga en DuckDB con dos vistas
analíticas, y una API FastAPI que traduce preguntas en español a SQL con Gemini,
**valida la consulta antes de ejecutarla** y la corre sobre una conexión de solo
lectura.

```bash
curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
     -d '{"question": "¿Cuánto vendimos en España en marzo?"}'
```
```json
{
  "question": "¿Cuánto vendimos en España en marzo?",
  "sql": "SELECT importe_total FROM v_ventas_mes_pais WHERE pais = 'España' AND mes = '2024-03-01' LIMIT 500",
  "row_count": 1,
  "rows": [{"importe_total": 10641.01}]
}
```

Salida real, no inventada: el `LIMIT 500` lo añade el validador, no el modelo.

## Cómo ejecutarlo

Requiere **Python 3.10 o superior**.

```bash
git clone https://github.com/marym931015/pedidos-nlq.git && cd pedidos-nlq
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python src/generate_data.py     # crea data/orders_raw.csv (1.240 filas con errores)
python -m src.etl               # limpia, carga en DuckDB y crea las vistas

pytest -q                       # los tests pasan SIN clave de API

cp .env.example .env            # y pon tu GEMINI_API_KEY (aistudio.google.com/apikey)
uvicorn src.api:app --reload    # http://localhost:8000/docs
```

Sin `GEMINI_API_KEY` todo funciona menos `/ask`, que responde `503` con un error
explícito. Es deliberado: el proyecto se puede clonar, cargar y testear sin tener
cuenta en Google.

## Dónde está cada cosa que pide el enunciado

| Se pedía | Dónde está |
|---|---|
| CSV de ≥1.000 pedidos con duplicados, fechas mal escritas e importes vacíos | `src/generate_data.py` → 1.240 filas, errores contados |
| Script que lo limpie y lo guarde en una BD | `src/clean.py` + `src/etl.py` → DuckDB, con informe de calidad |
| Dos vistas: ventas por mes y país, top 10 clientes | `src/views.sql` |
| API FastAPI con endpoint que recibe lenguaje natural | `POST /ask` en `src/api.py` |
| Usa un LLM para convertirla en consulta | `src/llm.py` (Gemini Flash Lite) |
| La valida antes de ejecutarla: solo lectura y tablas permitidas | `src/guard.py` (AST + allowlist + `read_only`) |
| Si el LLM falla o devuelve algo no válido, error claro | tabla de errores más abajo: 503 / 422 / 400 tipados |
| Al menos 3 tests: limpieza, validación y endpoint con LLM simulado | `tests/` → los tres, con 48 casos |
| README con ejecución, decisiones, con más tiempo y uso de la IA | este documento |
| No subir la clave al repositorio | `.env` en `.gitignore` desde el primer commit |
| Commits a medida que se avanza | 12 commits, uno por pieza, incluidos los `fix:` reales |

## Arquitectura

```
generate_data.py → data/orders_raw.csv
                        │
                   clean.py (limpieza pura, sin BD)  ←── test_cleaning.py
                        │
                     etl.py → orders.duckdb
                               ├─ orders            (limpia, tipada)
                               ├─ orders_rejected   (lo descartado, con motivo)
                               ├─ v_ventas_mes_pais ─┐ únicas tablas
                               └─ v_top_clientes   ──┘ visibles para la API
                        │
      api.py  POST /ask ─→ llm.py (Gemini) ─→ guard.py ─→ conexión read_only
                               ↑                  ↑              ↑
                          503 si falla    400/422 si el SQL   no puede escribir
                                          no es admisible     aunque todo falle
```

La idea central: **el LLM no tiene ningún permiso**. Genera texto, y ese texto
atraviesa dos barreras independientes antes de tocar la base de datos.

## Estructura del repo

```
src/
  generate_data.py   genera el CSV con errores controlados (semilla fija)
  clean.py           limpieza pura: DataFrame → (válidas, rechazadas, informe)
  etl.py             orquesta: CSV → clean → DuckDB → vistas
  db.py              conexión (read_only para la API) y allowlist de vistas
  views.sql          las dos vistas, en SQL y no incrustadas en Python
  guard.py           validador del SQL del LLM
  llm.py             Gemini: pregunta → SQL
  api.py             FastAPI: /ask, /health, /schema
tests/
  conftest.py        BD temporal con las vistas reales
  test_cleaning.py   test_guard.py   test_api.py
data/
  sample_orders.csv  20 filas de muestra, versionadas para verlas en GitHub
```

`clean.py` está separado de `etl.py` a propósito: la limpieza no sabe que existe
una base de datos, así que se puede testear con un DataFrame de 8 filas sin
levantar nada.

## Esquema de datos

`orders` — lo que sobrevive a la limpieza:

| columna | tipo | nota |
|---|---|---|
| `order_id` | VARCHAR | único; los duplicados se eliminan por aquí |
| `fecha` | DATE | parseada desde 4 formatos distintos |
| `cliente`, `pais`, `producto` | VARCHAR | normalizados (espacios, mayúsculas, acentos) |
| `importe` | DECIMAL(10,2) | `NULL` si venía vacío |
| `estado` | VARCHAR | `entregado`, `enviado`, `pendiente`, `cancelado`, `devuelto` |

`orders_rejected` — lo que no se pudo salvar, con el valor original intacto y una
columna `motivo` (`fecha_irrecuperable`, `importe_negativo`, `estado_desconocido`).
Ninguna de las dos tablas es accesible desde la API.

## Decisiones

**DuckDB en vez de Postgres.** Embebida, un solo fichero, cero instalación: quien
evalúe el proyecto lo ejecuta con `pip install` y dos comandos. Además tiene tipos
`DATE` y `DECIMAL` reales (SQLite no), así que `date_trunc('month', fecha)` y las
sumas de importes son SQL normal y no aritmética sobre texto. Para un sistema real
multiusuario usaría Postgres; aquí la portabilidad vale más que la concurrencia.

**Importes vacíos → `NULL`, nunca `0`.** Un `0` entra en las sumas y en las medias
y falsea los agregados en silencio; un `NULL` queda fuera y se puede contar. El
informe de calidad dice cuántos hubo.

**Nada se pierde en silencio.** Lo que no se puede reparar no se descarta: va a
`orders_rejected` con el motivo (`fecha_irrecuperable`, `importe_negativo`,
`estado_desconocido`). Un ETL que tira filas sin dejar rastro es un ETL que miente.
El test `test_ninguna_fila_se_pierde_en_silencio` comprueba el cuadre:
válidas + rechazadas + duplicados = filas leídas.

**La API solo ve las dos vistas.** Ni `orders` ni `orders_rejected` están en la
allowlist. Las vistas son agregados, así que el LLM no puede extraer datos fila a
fila ni aunque genere SQL válido. La lista de lo permitido es pequeña y defendible.

**Validación por AST, no por regex.** `guard.py` parsea el SQL con `sqlglot` y
pregunta al árbol qué tablas toca. Buscar palabras en el texto falla en los tres
sentidos: `SELECT * FROM v_top_clientes -- ; DROP TABLE orders` engaña a quien
parte por `;`, `SELECT/*DROP*/ ...` dispara falsos positivos, y una subconsulta
`WHERE cliente IN (SELECT ... FROM orders)` no aparece en el `FROM` principal.
Todos esos casos están en `test_guard.py`.

**La lista negra es específica de DuckDB.** Un `SELECT` no es inofensivo aquí:
`SELECT * FROM read_csv_auto('/etc/passwd')` lo es, y `getenv('GEMINI_API_KEY')`
también. Por eso el validador bloquea las funciones que tocan el sistema de
ficheros y el entorno, además de exigir `SELECT` y allowlist de tablas.

**Conexión en modo `read_only`.** Defensa en profundidad: aunque el validador
tuviera un fallo, DuckDB rechaza cualquier escritura a nivel de conexión.

**`LIMIT` forzado a 500.** No es seguridad sino contención: una pregunta ambigua
no puede devolver la tabla entera por HTTP. Si el modelo pone un límite mayor, se
recorta.

**Cancelados y devueltos no son ventas.** Están en `orders` porque ocurrieron,
pero las dos vistas los excluyen; sumarlos inflaría los ingresos.

**Errores tipados, nunca trazas.** `503 llm_unavailable` (proveedor caído o sin
clave), `422 sql_no_parseable` / `question_out_of_scope` (el modelo devolvió algo
inservible), `400 tabla_no_permitida` / `solo_lectura` (SQL válido pero prohibido).
Ninguna respuesta incluye stack traces ni variables de entorno.

**Rangos (`>=`) en requirements.txt**, no versiones fijadas: prioriza que instale
a la primera en la máquina de quien evalúa. En producción usaría un lockfile.

## Las dos vistas

| Vista | Qué responde |
|---|---|
| `v_ventas_mes_pais` | ventas por mes y país: nº de pedidos, importe total, ticket medio |
| `v_top_clientes` | los 10 clientes que más compran, con ticket medio y última compra |

Ambas excluyen `cancelado` y `devuelto`, y los importes `NULL` no entran en las
sumas. Salida real sobre los datos generados:

```
v_ventas_mes_pais (72 filas)
┌────────────┬──────────┬─────────────┬───────────────┬──────────────┐
│    mes     │   pais   │ num_pedidos │ importe_total │ ticket_medio │
├────────────┼──────────┼─────────────┼───────────────┼──────────────┤
│ 2024-01-01 │ Alemania │           9 │       9839.37 │      1093.26 │
│ 2024-01-01 │ España   │          14 │       8148.29 │       582.02 │
│ 2024-01-01 │ Francia  │          12 │      10164.26 │       847.02 │
└────────────┴──────────┴─────────────┴───────────────┴──────────────┘

v_top_clientes (10 filas)
┌───────────────┬─────────────┬───────────────┬──────────────┬───────────────┐
│    cliente    │ num_pedidos │ importe_total │ ticket_medio │ ultima_compra │
├───────────────┼─────────────┼───────────────┼──────────────┼───────────────┤
│ Pablo Lopez   │          20 │      15173.83 │       758.69 │ 2024-12-24    │
│ Carlos Romero │          17 │      14885.06 │       875.59 │ 2024-12-20    │
│ Diego Nunez   │          16 │      14516.67 │       907.29 │ 2024-12-28    │
└───────────────┴─────────────┴───────────────┴──────────────┴───────────────┘
```

## La API

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/ask` | pregunta en lenguaje natural → SQL validado → filas |
| `GET` | `/health` | comprobación de vida y vistas disponibles |
| `GET` | `/schema` | el esquema exacto que se le inyecta al LLM |
| `GET` | `/docs` | Swagger generado por FastAPI |

La respuesta de `/ask` incluye siempre el SQL que se ejecutó, no solo el
resultado: quien consume la API puede auditar qué hizo el modelo.

### Errores

Cada fallo tiene su código y su causa, y ninguno devuelve trazas ni variables de
entorno:

| HTTP | `error` | Cuándo |
|---|---|---|
| 503 | `llm_unavailable` | sin `GEMINI_API_KEY`, o el proveedor falla o no responde |
| 422 | `question_out_of_scope` | el modelo responde que la pregunta no se cubre con las vistas |
| 422 | `sql_no_parseable` | el modelo devolvió texto que no es SQL |
| 422 | `multiples_sentencias` | llegaron dos sentencias en una |
| 400 | `tabla_no_permitida` | SQL válido contra `orders` u otra tabla fuera de la allowlist |
| 400 | `solo_lectura` | el modelo devolvió `DROP`, `INSERT`, `COPY`, `ATTACH`… |
| 400 | `sql_execution_error` | el SQL pasó el validador pero DuckDB lo rechazó |
| 422 | — | la pregunta no llega a 3 caracteres (la para Pydantic, sin gastar una llamada al LLM) |

```bash
$ curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
       -d '{"question": "dame el detalle de cada pedido individual de Ana García"}'
{"error":"question_out_of_scope","detail":"La pregunta no se puede responder con las vistas disponibles."}

$ curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
       -d '{"question": "ab"}'
HTTP 422 — la para Pydantic, sin gastar una llamada al modelo
```

Las vistas son agregados, así que pedir el detalle fila a fila no tiene respuesta
posible: el modelo lo reconoce y la API lo dice con claridad en vez de devolver
una lista vacía.

## Informe de calidad

`python -m src.etl` imprime lo que arregló, y los errores del generador están
contados con semilla fija, así que es reproducible:

```
$ python -m src.etl

INFORME DE CALIDAD
---------------------------------------------------
Filas leidas del CSV                          1240
Duplicados eliminados (mismo order_id)          40
Fechas reparadas (formato no ISO)               17
Fechas irrecuperables -> rechazadas             33
Importes reparados (simbolo o coma decimal)     35
Importes vacios -> NULL (no 0)                  25
Importes negativos -> rechazados                10
Paises normalizados                             70
Clientes normalizados                           70
Estados desconocidos -> rechazados               0
FILAS CARGADAS en orders                      1157
FILAS EN orders_rejected                        43

Base de datos: orders.duckdb
  v_ventas_mes_pais          72 filas
  v_top_clientes             10 filas
```

Las cifras cuadran con lo que inyecta el generador: 17 fechas reparadas + 33
rechazadas = las 50 fechas malas; 35 importes reparados + 25 vaciados a `NULL` =
los 60 importes con formato roto, de los cuales 10 eran negativos y se rechazan.
Y 1157 + 43 + 40 = 1240, el total leído: ninguna fila se pierde por el camino.

## Tests

```bash
pytest -q
```

**48 casos en 3,6 segundos.** Los tres que pide el enunciado son uno por fichero:

| Fichero | El test pedido | Qué más cubre |
|---|---|---|
| `test_cleaning.py` | la limpieza | duplicados, fechas recuperables vs irrecuperables, importes vacíos y negativos, normalización, y el cuadre de filas (nada se pierde) |
| `test_guard.py` | la validación de la consulta | 16 casos peligrosos: DML, dos sentencias, tablas prohibidas en `JOIN` y en subconsulta, funciones de fichero de DuckDB, comentarios, `LIMIT` |
| `test_api.py` | el endpoint con el LLM simulado | éxito (200), LLM caído (503), respuesta no-SQL (422), tabla prohibida (400), intento de escritura (400), pregunta vacía (422) |

El que mejor defiende el diseño es `test_sql_contra_tabla_prohibida_devuelve_400`:
el modelo devuelve SQL correcto, ejecutable y sintácticamente impecable contra
`orders` — y aun así se rechaza. La barrera no depende de que el LLM se porte bien.

Ningún test llama a Gemini: `llm.generar_sql` se sustituye con `monkeypatch`. Son
rápidos, deterministas y no cuestan dinero.

## Limitaciones conocidas

Asumidas a propósito para no pasarme del tiempo de la prueba:

- **La calidad del NL→SQL no está medida.** Funciona en las preguntas que probé,
  pero no tengo un conjunto de evaluación que diga cuántas acierta.
- **Sin autenticación ni límite de peticiones**: cada llamada a `/ask` gasta
  cuota del modelo, así que un endpoint abierto no es publicable tal cual.
- **Un solo escritor**: DuckDB bloquea el fichero. Si el ETL se está ejecutando,
  la API no puede abrir la base de datos.
- **Sin timeout de consulta**: DuckDB no tiene `statement_timeout`; la contención
  es el `LIMIT 500` que fuerza el validador sobre dos vistas ya agregadas.
- Los datos son sintéticos y en euros, sin conversión de divisa.

## Con más tiempo

- **Evaluación del NL→SQL**: un set de preguntas con su SQL esperado y una métrica
  de acierto. Hoy no sé medir si el prompt empeora al tocarlo.
- **Few-shot en el prompt** con 3-4 pares pregunta→SQL; es la mejora de calidad
  más barata.
- **Caché** de pregunta→SQL: las mismas preguntas se repiten y cada una cuesta una
  llamada al modelo.
- **Observabilidad**: registrar cada SQL generado, si pasó el validador y cuánto
  tardó. Es lo que permitiría afinar el prompt con datos reales.
- Autenticación y rate limiting en `/ask`, Docker, CI en GitHub Actions, y
  migración a Postgres si hubiera escrituras concurrentes.

## Cómo usé la IA

Usé Claude (Claude Code) como par de programación durante todo el proyecto, y los
commits los hice yo a mano para controlar qué entraba en cada uno. Lo relevante no
es que lo usara, sino en qué lo usé, qué di por bueno y qué no.

### Qué le pedí

- **Primero, un plan, no código.** Le di el enunciado y le pedí que lo dividiera en
  piezas con un reparto de tiempo, y que me dijera qué se evalúa en cada una. De ahí
  salió el orden de trabajo, y la decisión de escribir el validador *antes* que el
  endpoint para que cada commit tuviera una sola responsabilidad.
- **Que me explicara DuckDB antes de elegirla.** No la había usado nunca. Pedí
  comparación con Postgres y SQLite, qué pierdo y qué gano, y en qué casos *no* la
  usaría. Elegí DuckDB con ese criterio, no porque me la propusieran.
- **Generación de código pieza a pieza**, con una regla: que cada decisión no obvia
  llevara un comentario explicando el *por qué*, no el *qué*.
- **Revisión adversarial del validador**: le pedí explícitamente que intentara
  saltárselo y que escribiera los tests desde ese ángulo. De ahí salieron los casos
  del comentario `--`, la subconsulta y las funciones de fichero de DuckDB.
- **Diagnóstico cuando algo fallaba**: el modelo de Gemini que devolvía 404, el
  `pip install` que no avanzaba (resultó ser la red a 50 KB/s), el tipo `TIMESTAMP`
  inesperado en la vista.

### Qué acepté tal cual

Lo acepté donde el coste de equivocarse es bajo y la verificación es inmediata:

- **El boilerplate de FastAPI**: modelos Pydantic, `lifespan`, `TestClient`. Es
  código estándar y los tests lo cubren; revisarlo línea a línea no aporta nada.
- **El generador de datos sintéticos.** Si genera mal, lo veo en el CSV al instante.
- **La estructura de carpetas** y la separación en módulos, porque coincidía con lo
  que yo habría hecho y hace los tests más simples.
- **El parseo de fechas en cascada con pandas**, tras comprobar en el informe de
  calidad que los números cuadraban con los errores inyectados.

### Qué corregí

**Antes de ejecutar, revisando el código:**

- La primera propuesta de validación era por **lista de palabras prohibidas sobre el
  string**. La rechacé: no cubre comentarios ni subconsultas. Pasamos a parsear con
  `sqlglot` y preguntar al AST qué tablas se tocan de verdad.
- La lista negra era la **genérica de SQL** (`DROP`, `DELETE`, `INSERT`…). Le faltaba
  lo propio de DuckDB — `read_csv_auto`, `getenv`, `ATTACH`, `COPY` — que permite
  leer ficheros del servidor desde un `SELECT` aparentemente inocuo. Esto solo se ve
  si entiendes la herramienta que elegiste.
- El generador producía las fechas malas **en un solo formato alternativo**. Forcé
  variedad real (europeo, US, con puntos, imposibles y vacías) para que el ETL
  tuviera que distinguir entre *reparable* e *irrecuperable*, que es lo que justifica
  la tabla de rechazos.
- Los **importes vacíos se convertían a `0`**. Lo cambié a `NULL`: un 0 entra en las
  sumas y en las medias y falsea los agregados sin avisar.
- Puso **`SET statement_timeout`** en el endpoint. Eso es de PostgreSQL; DuckDB no lo
  soporta y habría hecho fallar **todas** las peticiones con un error de catálogo. Lo
  detecté revisando, antes de llegar a ejecutarlo.
- Exigí que **los tests no llamaran a Gemini** y que el **SQL ejecutado se devolviera
  en la respuesta**, para que la API sea auditable.

**Ya con el sistema en marcha** — esta es la parte que más enseñó:

- `date_trunc('month', fecha)` devuelve **`TIMESTAMP`, no `DATE`**. El prompt le decía
  al modelo que `mes` era `DATE`, así que `WHERE mes = DATE '2024-03-01'` no casaba.
  Lo arreglé en la vista con un cast, no en el prompt: el tipo correcto es
  responsabilidad del modelo de datos, no del que escribe la pregunta.
- **El prompt no decía qué periodo cubren los datos.** El modelo generaba SQL
  impecable contra `mes = '2023-03-01'` y devolvía cero filas — el peor fallo
  posible, porque no da error, da vacío. Añadí al prompt el rango real de fechas y
  los países presentes, leídos de la propia base de datos al arrancar.
- En una prueba contra Gemini el modelo escribió `FROM v_v_ventas_mes_pais`, con el
  prefijo duplicado. **El validador lo paró con 400 `tabla_no_permitida`.** No es un
  caso inventado para un test: ocurrió ejecutando la API de verdad, y es la mejor
  prueba de por qué el SQL de un LLM no puede llegar directo a la base de datos.
- El modelo de Gemini del primer borrador (`gemini-2.5-flash`) ya no está disponible
  para cuentas nuevas: Google devuelve 404. Lo verifiqué listando los modelos que mi
  clave acepta y fijé uno vigente.

**Lo que no delegué**: la elección de base de datos, el criterio de qué cuenta como
venta, qué se rechaza y con qué motivo, la decisión de que la API solo vea vistas
agregadas, y el contenido de este README.

## Seguridad de la clave

`.env` entró en `.gitignore` en el primer commit, antes de que existiera ninguna
clave. Comprobación de que no se coló en el historial:

```bash
git log -p | grep -iE "AIza|api[_-]?key *= *[^ ]" || echo "limpio"
```

**Tiempo invertido**: entre 2 h y 2 h 30 de trabajo efectivo. De reloj fueron casi
3 horas, pero ahí dentro hay una pausa por un traslado y bastante rato esperando a 
que se instalaran las dependencias con una conexión un poco lenta.
