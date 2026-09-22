# API SEI — Extractor da Caixa de Processos

Conjunto de scripts en Python que automatizan el **login en el SEI-MG (GOVMG)**,
cambian a la unidad de **FHEMIG** y **raspan solamente el listado de la caixa de
"Procesos Recibidos"** (Control de Procesos), **sin abrir ningún proceso**.

El código nunca accede a `procedimento_trabalhar`, `documento_visualizar` ni a
ninguna pantalla que marque o altere el estado de un proceso. Un sistema de
**bloqueos de seguridad** (whitelist + blacklist de URLs) lo garantiza.

---

## Contenido

- [Contexto y objetivo](#contexto-y-objetivo)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos e instalación](#requisitos-e-instalación)
- [Configuración (.env)](#configuración-env)
- [Cómo funciona cada script](#cómo-funciona-cada-script)
  - [`app.py` — login simple](#apppy--login-simple)
  - [`app2.py` — extracción con hash fijo](#app2py--extracción-con-hash-fijo)
  - [`app3.py` — extracción robusta con bloqueos de seguridad](#app3py--extracción-robusta-con-bloqueos-de-seguridad)
  - [`api.py` — API FastAPI](#apipy--api-fastapi)
- [Bloqueos de seguridad (app3)](#bloqueos-de-seguridad-app3)
- [Flujo de datos de cada script](#flujo-de-datos-de-cada-script)
- [Uso de la API](#uso-de-la-api)
- [Observaciones y avisos](#observaciones-y-avisos)

---

## Contexto y objetivo

La FHEMIG usa el SEI-MG para tramitar procesos. La caixa **Procesos Recibidos**
lista los procesos que llegan a la unidad, pero el SEI no ofrece una API
pública para exportar esos datos. Estos scripts automatizan la lectura
**solo de las pantallas de listado** y exportan la información visible en
planillas (`.xlsx`) y/o a través de una API HTTP.

> **IMPORTANTE**: los scripts **no entran en procesos** y **no** modifican
> nada en la unidad. Solo leen el listado (con paginación) y graban archivos
> locales.

---

## Estructura del proyecto

| Archivo | Rol |
|---|---|
| `app.py` | Ejemplo mínimo de login automático en el SEI (guarda `resposta_login.html`). |
| `app2.py` | Versión con cambio de unidad (hash fijo) + raspado de N páginas y generación de planilla de muestra. |
| `app3.py` | Versión principal y robusta: login, cambio de unidad **con hash dinámico**, bloqueos de seguridad, auditoría y extracción de la caixa de recibidos con filtro. |
| `api.py` | API **FastAPI** que expone el raspado de `app3.py` como trabajos/rutas HTTP (JSON + descarga de Excel). |
| `.env` | Credenciales `SEI_USUARIO` / `SEI_SENHA` (no va al Git). |
| `.gitignore` | Lista archivos/carpetas ignorados (`.env`, `venv/`, `__pycache__/`...). |
| `log_auditoria.txt` | Log gravado por `app3.py` con todas las solicitudes (auditoría). |
| `*.html` | Páginas guardadas del SEI (usadas para pruebas offline y depuración). |
| `*.xlsx` | Planillas generadas en ejecuciones anteriores. |

---

## Requisitos e instalación

Las bibliotecas utilizadas son:

- `requests`
- `beautifulsoup4` (`bs4`)
- `pandas`
- `openpyxl` (generación del `.xlsx`)
- `fastapi` + `uvicorn` + `pydantic` (solo para `api.py`)

Instalación (con entorno virtual):

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# o: source venv/bin/activate   (Linux/macOS)

pip install requests beautifulsoup4 pandas openpyxl
pip install fastapi uvicorn pydantic   # solo para la API
```

---

## Configuración (.env)

Cree un archivo `.env` al lado de los scripts con:

```env
SEI_USER=su_usuario
SEI_SENHA=su_contrasena
```

- El archivo `.env` **no está versionado** (está en `.gitignore`).
- Escriba los valores sin comillas; el script elimina comillas dobles/simples
  automáticamente.
- Si no existe `.env`, las variables también pueden venir del entorno
  (variables de ambiente).

---

## Cómo funciona cada script

### `app.py` — login simple

Flujo mínimo de autenticación:

1. Abre `LOGIN_URL` para recibir las cookies de sesión.
### `app2.py` — extracción con hash fijo

Va más lejos que `app.py`:

1. **Login** automático.
2. **Cambio de unidad** `110007141` usando un `infra_hash` **fijo** capturado
   de la pantalla.
3. Accede a la raíz (`inicializar.php`) para que el SEI genere un nuevo hash.
4. Encuentra el enlace del **Control de Procesos** y navega el listado.
5. Raspa **N páginas** de la caixa (`extrair_todas_as_paginas`, por defecto 5).
6. Filtra procesos que comienzan con **1080** y genera
   `amostra_judiciais_1080_nova_unidade.xlsx`.

Ejecución:

```bash
venv\Scripts\python app2.py
```

### `app3.py` — extracción robusta con bloqueos de seguridad

Es la versión **principal** (también importada por `api.py`). Pasos:

1. Lee credenciales del `.env`.
2. **Autentica** en el SEI (con auditoría en `log_auditoria.txt`).
3. Detecta la unidad actual y **cambia de unidad** a FHEMIG (`110007141`)
   usando el **hash dinámico** de la propia pantalla (no fijo).
4. Navega al **Control de Procesos** (`inicializar.php` → enlace con
   `acao=procedimento_controlar`).
5. **Raspa hasta N páginas** (por defecto 5) de la caixa "Procesos Recibidos",
   soportando las vistas **Resumida** y **Detallada** del SEI.
6. Convierte a `pandas.DataFrame`, aplica **filtro por prefijo** ("1080") y
   genera `caixa_recebidos_fhemig_1080.xlsx` (o `caixa_recebidos_todos.xlsx`
   con `--todos`).

Ejemplos de ejecución:

```bash
venv\Scripts\python app3.py                # por defecto: filtro 1080, 5 páginas
venv\Scripts\python app3.py --todos         # mantiene todos los procesos
venv\Scripts\python app3.py --offline resposta_login.html   # prueba 100% offline
```

- `--offline [archivo.html]` procesa un HTML ya guardado (no toca el SEI).
- `--todos` ignora el filtro `1080` y considera todos los números.

**Columnas extraídas** por línea:

- `Processo` — número visible en el listado
- `NumeroCheckbox` / `IdProtocolo` — id interno del checkbox/enlace
- `Tipo`, `Especificacao` — tipo/especificación del proceso
- `NaoVisualizado`, `NaoRecebido` — flags de estado
- `AtribuidoA`, `CpfAtribuido` — usuario/persona a quien está asignado
- `Anotacoes`, `Marcadores`, `Sinalizadores`
- `Pagina` — página en la que fue encontrado

### `api.py` — API FastAPI

Los bloques de `app3.py` se exponen vía **FastAPI** con **trabajos en
segundo plano**. El raspado corre en un hilo y el cliente consulta/descarga el
resultado.

Ejecución:

```bash
venv\Scripts\python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

o directamente:

```bash
venv\Scripts\python api.py
```

La API queda disponible en `http://127.0.0.1:8000` (docs interactivas en
`http://127.0.0.1:8000/docs`).
2. Guarda manualmente la cookie `SIP_U_GOVMG_SEI` (la que crearía el JS del SEI).
3. Envía el formulario de login (`txtUsuario`, `pwdSenha`, `selOrgao`, `hdnAcao`).
4. Verifica si la respuesta contiene "sair.php" / "Sair" y guarda `resposta_login.html`.

Ejecución:

```bash
venv\Scripts\python app.py
```

Este script es didáctico (un ejemplo de login); para extracción real use el
`app3.py`.