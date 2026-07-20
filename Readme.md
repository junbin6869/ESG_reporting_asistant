# ESG Assistant

Invoice-based ESG classification and reporting system built with Next.js,
FastAPI, LangChain, LangGraph, Chroma, and SQLite.

## Architecture

- **Next.js** provides invoice upload, review, dashboard, batch progress, and
  report screens.
- **FastAPI** exposes invoice, classification, guideline, report, and MCP APIs.
- **LangChain** owns the shared chat model, structured invoice-classification
  chain, embeddings, Chroma vector store, and Retriever interface.
- **LangGraph** orchestrates the stateful report workflow: prepare invoices,
  decide whether to retrieve, search, validate, and save.
- **SQLite** stores invoices, versioned classifications, batch/job state,
  reports, and report-run progress. LangGraph checkpoints use a separate local
  SQLite file.

## Run with Docker (recommended)

Docker packages the Python and Node.js runtimes, Python and npm dependencies,
Tesseract OCR, and the local embedding model. The backend pins a CPU-only
PyTorch build because the default embedding workload does not require CUDA. A
new user only needs the project, [Docker Desktop](https://www.docker.com/products/docker-desktop/),
and an OpenAI API key; Python, Node.js, and Tesseract do not need to be installed
separately on the host computer.

From the project root, create the local environment file and replace the sample
key in `.env`:

```powershell
Copy-Item .env.example .env
notepad .env
```

The real `.env` is excluded from both Git and the Docker build context. The
OpenAI key is passed to the backend container only at runtime and is not baked
into either image.

### First start

Make sure Docker Desktop is running, then build and start the complete system:

```powershell
docker compose up --build
```

The first build takes longer because Docker downloads the base images, installs
all dependencies and OCR libraries, and prepares the embedding model. The
backend then builds the Chroma guideline index from `PDF/index_manifest.txt`
automatically and idempotently. Keep the first terminal open to see startup
progress.

When both services are healthy, open:

- Frontend: `http://localhost:3000`
- Backend API docs: `http://localhost:8000/docs`
- Backend health check: `http://localhost:8000/health`

### Daily use

After the images have been built once, start in the background with:

```powershell
docker compose up -d
```

Useful commands:

```powershell
# Show service and health status
docker compose ps

# Follow both services' logs; press Ctrl+C to stop following
docker compose logs -f

# Stop and remove containers/network while keeping application data
docker compose down
```

Docker Desktop must be running whenever the application is used. Closing the
browser does not stop the containers.

### Docker Desktop troubleshooting

An error mentioning `dockerDesktopLinuxEngine`, `docker_engine`, `npipe`, or
"daemon is not running" means the Docker command-line client is installed but
the Docker Engine is not ready. Open Docker Desktop, wait for it to finish
starting, and verify it before retrying Compose:

```powershell
docker info
docker compose up -d
```

Keep several GB free on the drive used by Docker. This backend includes PyTorch,
OCR libraries, and a local embedding model, so its first build needs more
temporary space than the final image. On Windows with WSL 2, Docker stores its
data under the user profile by default. To use a larger drive, use Docker
Desktop **Settings > Resources > Advanced > Disk image location**; do not move
the VHDX manually. The command below removes only unused build cache, not named
volumes, when old builds consume too much space:

```powershell
docker builder prune --all
```

### Rebuild after changes

Application source is copied into the images, so rebuild after changing source
code, a Dockerfile, `requirements.txt`, `package.json`, or `package-lock.json`:

```powershell
docker compose up -d --build
```

`NEXT_PUBLIC_API_BASE_URL` is compiled into the frontend. Changing it in `.env`
also requires the rebuild command above. A complete cache-free rebuild is only
normally needed when diagnosing a stale image:

```powershell
docker compose build --no-cache
docker compose up -d
```

### Persistent data and backups

Docker Compose creates three named volumes that survive `docker compose down`,
container replacement, and image rebuilds:

| Volume | Container path | Contents |
| --- | --- | --- |
| `backend_data` | `/app/data` | invoices, classifications, reports, jobs, and LangGraph checkpoints |
| `chroma_data` | `/app/chroma_db` | the generated Chroma guideline index |
| `backend_logs` | `/app/logs` | MCP and report-agent audit logs |

The host `PDF` directory is mounted at `/app/PDF` read-only. Edit PDFs and
`PDF/index_manifest.txt` on the host; the container cannot modify them. The
embedding model is part of the backend image, so mounting an empty Hugging Face
cache over it is intentionally avoided.

English OCR is installed by default. To add Malay, for example, set
`TESSERACT_LANGUAGE_PACKAGES="tesseract-ocr-eng tesseract-ocr-msa"` and
`OCR_LANGUAGES=eng+msa` in `.env`, then rebuild the backend image. The first
value installs Tesseract's language data; the second tells the application
which installed languages to use.

Stop the services before copying a consistent SQLite backup, then copy the
three persistent directories to a host folder:

```powershell
docker compose stop
New-Item -ItemType Directory -Force -Path .\backup\data, .\backup\chroma_db, .\backup\logs | Out-Null
docker compose cp backend:/app/data/. .\backup\data
docker compose cp backend:/app/chroma_db/. .\backup\chroma_db
docker compose cp backend:/app/logs/. .\backup\logs
docker compose start
```

Do **not** run `docker compose down -v` unless all application data should be
permanently deleted. The `-v` option removes the named volumes. A normal
`docker compose down` is safe for persisted data.

The first Docker start creates fresh named volumes; it does not automatically
copy an older native `backend/data` directory. To keep an existing local
database, stop the native backend first, start Docker once to create its
container, stop the Docker backend, and copy the directory into its volume:

```powershell
docker compose up -d
docker compose stop backend
docker compose cp .\backend\data\. backend:/app/data
docker compose start backend
```

Chroma does not need to be migrated: with `AUTO_BUILD_VECTOR_DB=true`, it is
rebuilt from the read-only PDF sources when its volume is empty.

## Manual install (without Docker)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If this checkout already uses `backend/venv`, substitute `venv` for `.venv` in
the commands; `.venv` is the recommended name for new environments.

Create a project-root `.env`:

```dotenv
OPENAI_API_KEY=your-key
LLM_MODEL=gpt-4o-mini
FRONTEND_ORIGIN=http://localhost:3000
CLASSIFICATION_WORKER_COUNT=3
```

`EMBEDDING_LOCAL_FILES_ONLY=true` is the default so a deployed app does not
silently contact Hugging Face. Set it to `false` once when the configured model
must be downloaded on a new machine, then return it to `true`.

## Build or refresh the guideline index

The existing `chroma_db` remains compatible. Indexing is idempotent and only
re-embeds a source when its bytes or chunking version change.

```powershell
# From the project root
backend\.venv\Scripts\python.exe build_vector_db.py

# Rebuild every source intentionally
backend\.venv\Scripts\python.exe build_vector_db.py --reset
```

`PDF/index_manifest.txt` is the default ingestion allowlist. Add only
ingest-ready ESG PDFs there; `PDF/BursaMalaysia.pdf` is retained as raw source
material and is deliberately excluded to avoid duplicate/noisy chunks.

## Run

```powershell
# Terminal 1
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000

# Terminal 2
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. The frontend expects the API at
`http://localhost:8000` unless `NEXT_PUBLIC_API_BASE_URL` is configured.

On startup, the backend applies additive SQLite migrations, recovers expired
classification leases, resumes queued classification work, and restores
incomplete LangGraph report runs.

Run this SQLite build as one Uvicorn process. `CLASSIFICATION_WORKER_COUNT` is
per process; a multi-process deployment should first move job leasing and
LangGraph checkpoints to shared production infrastructure such as Postgres.

## Batch invoice import

Upload a JSON array or `{ "invoices": [...] }` in the UI. The frontend sends one
request rather than one request per invoice:

```http
POST /invoice-batches
GET  /invoice-batches/{batch_id}
```

Each invoice version gets an idempotent persistent classification job. A change
to classification inputs increments the invoice version and queues a new job;
the previous classification remains as audit history but is no longer treated
as current. Malformed items are reported without rolling back valid items. The
UI polls with backoff, tolerates transient errors, and stores the active batch
ID in the browser so tracking can resume after a refresh.

`invoice_number` is currently the global idempotency key. If two suppliers can
issue the same number, prefix it with a supplier/source code before import.

PDF extraction is limited to 10 MB and 20 pages per file. The browser processes
at most three PDF previews concurrently and presents completed previews for
review as they arrive.

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

cd ..\frontend
npx tsc --noEmit
npm run build
```

The backend suite includes a 100-invoice batch import, concurrent job claims,
retry limits, LangChain Retriever/structured-output tests, idempotent indexing,
and persistent LangGraph execution.

## MCP server

```powershell
cd backend
.\.venv\Scripts\python.exe mcp_server.py
```

MCP audit logs and report-agent audit logs are stored under `backend/logs/` and
are intentionally excluded from Git.

## File hygiene

- `backend/app/infrastructure/` is the canonical vector/index implementation.
  Root `vector_db.py` and `build_vector_db.py` are small compatibility entry
  points, not duplicate implementations.
- `Invoice/*.json` are sample inputs. They are not loaded by the running API.
- `PDF/BursaMalaysia.pdf` is raw reference material; the manifest excludes it
  from Chroma. `PDF/Bursa_ESG_reordered_E_S_G.pdf` is the indexed source.
- `backend/image.png` is not referenced by the application. It was retained as
  a user asset and can be removed if it is only an old screenshot.
- virtual environments, caches, logs, SQLite data, `.next`, and `chroma_db` are
  generated runtime artifacts covered by `.gitignore`.
