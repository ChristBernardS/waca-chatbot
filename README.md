# WACA — UKDW Academic FAQ Chatbot

WACA is a three-stage NLP-to-SQL chatbot backend built for Universitas Kristen Duta Wacana (UKDW). It turns a free-text question into structured intent + entities, resolves that against PostgreSQL, and generates a natural-language answer — with rate limiting, an unanswered-question feedback loop, and an offline demo mode.

```
                      POST /chat
                          │
                          ▼
┌──────────────────────────────────────────────────┐
│  Stage 1 — Orchestration (api/orchestration.py)  │  ← Ollama Cloud LLM
│  user message → { intent, entities, confidence } │    (keyword fallback if Ollama is down)
└─────────────────────────┬────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────┐
│  Stage 2 — Retrieval (api/retrieval.py)          │  ← PostgreSQL (asyncpg)
│  intent + entities → handler → SQL → rows        │
└─────────────────────────┬────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────┐
│  Stage 3 — Response (api/response_layer.py)      │  ← Ollama Cloud LLM
│  rows + original question → NL answer            │    (template fallback if Ollama is down)
└──────────────────────────────────────────────────┘
```

If a question can't be answered, it's logged to `pertanyaan_tidak_terjawab` so an admin can review it and extend the knowledge base (`/admin/*` endpoints).

## Stack

| Component | Technology |
|---|---|
| API framework | FastAPI (lifespan-based startup, no deprecated `on_event`) |
| LLM | Ollama Cloud (`OLLAMA_BASE_URL=https://ollama.com`, model configurable via `.env`, e.g. `gemma3:27b-cloud`) |
| Database | PostgreSQL via `asyncpg` |
| Rate limiting | PostgreSQL (`rate_limits` table) as primary layer, optional Cloudflare Workers KV as edge layer |
| Frontend | Static HTML/CSS/vanilla JS (`ui/`), served by FastAPI at `/` |
| Evaluation | `eval_intent.py` — standalone intent-classification benchmark (54 test questions, confusion matrix, precision/recall/F1) |

## Supported Intents

Intent labels are in Indonesian and are validated against a fixed set (`api/orchestration.py`); anything else collapses to `general`.

| Intent | Primary table(s) | Covers |
|---|---|---|
| `layanan_akademik` | `pengetahuan` (biro_1) | KRS, transkrip, cuti akademik, surat keterangan aktif, presensi |
| `kemahasiswaan` | `pengetahuan` (biro_3) | Student affairs, asuransi/klaim |
| `kerjasama` | `pengetahuan` (biro_4, non-exchange) | Institutional/company partnerships |
| `student_exchange` | `pertukaran_mahasiswa` + `pengetahuan` (biro_4) | Inbound/outbound exchange, short-term, IISMA |
| `pendaftaran` | `jalur_pendaftaran` + `jadwal_pendaftaran` + `pengetahuan` (pmb) | Admission tracks and schedules |
| `biaya_kuliah` | `biaya_kuliah` | UKT/tuition by program and degree level |
| `program_studi` | `program_studi` | Faculties, majors, degree levels |
| `beasiswa` | `beasiswa` + `pengetahuan` (biro_3) | Scholarships — categorized `mahasiswa_baru` / `mahasiswa_aktif` / `eksternal` / `pinjaman` |
| `general` | `pengetahuan` (all units) | Anything that doesn't fit the above |

## Setup

### 1. Prerequisites

- PostgreSQL, running locally or reachable via `DATABASE_URL`
- An Ollama Cloud API key (or a self-hosted Ollama instance — point `OLLAMA_BASE_URL` at it)
- Python 3.12+

### 2. Configure environment

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

```
OLLAMA_BASE_URL=https://ollama.com
OLLAMA_API_KEY=your_key_here
OLLAMA_MODEL=gemma3:27b-cloud

DAILY_QUESTION_LIMIT=10

APP_HOST=0.0.0.0
APP_PORT=8000

DATABASE_URL=postgresql://user:password@localhost:5432/waca
```

Cloudflare Workers KV variables (`CF_*`) are optional — leave them unset and the rate limiter runs on PostgreSQL alone.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the API

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

On startup the app creates all tables (`_buat_skema`) and seeds them with UKDW data (`_isi_data`) if empty — no manual `CREATE DATABASE`/migration step beyond having an empty Postgres database for `DATABASE_URL` to point to.

### 5. Open the UI

Navigate to `http://localhost:8000`. The header shows a connection dot: green ("Connected") if `/health` responds, red ("Offline — Demo Mode") if the frontend falls back to canned responses without a backend.

## API Endpoints

### `POST /chat`
Main endpoint. Runs the full 3-stage pipeline.

```json
// Request
{
  "message": "Berapa biaya kuliah Informatika S1?",
  "session_id": "user-123",
  "history": []
}

// Response
{
  "reply": "...",
  "intent": "biaya_kuliah",
  "entities": { "nama_prodi": "Informatika", "jenjang": "S1" },
  "sql_query": "SELECT ... FROM biaya_kuliah WHERE ...",
  "raw_data": [...],
  "pipeline_steps": [
    { "stage": "M1_ORCHESTRATION", "status": "success", "detail": "..." },
    { "stage": "SQL_RETRIEVAL",    "status": "success", "detail": "..." },
    { "stage": "RESPONSE_LAYER",   "status": "success", "detail": "..." }
  ]
}
```

### `POST /chat/stream`
Same pipeline as `/chat`, streamed as Server-Sent Events.

### `POST /debug/m1`
Runs Stage 1 (orchestration) only — no SQL, no response generation. Returns the raw LLM output, parsing steps, and the final intent/entities after override rules. Useful for debugging intent classification without burning rate-limit quota.

### `GET /health`
Liveness probe — no DB or LLM call. Used by the frontend to decide Connected vs. Demo Mode.

### `GET /rate-limit-status`
Returns the current caller's remaining daily question quota without incrementing it.

### `GET /intents`
Lists all supported intents.

### Admin — unanswered questions
- `GET /admin/pertanyaan-tidak-terjawab?status=baru&limit=50&offset=0` — list logged questions the system couldn't answer (`status`: `baru` | `ditinjau` | `selesai` | `semua`)
- `PATCH /admin/pertanyaan-tidak-terjawab/{id}` — update `status` / `catatan_admin` after extending the knowledge base
- `GET /admin/pertanyaan-tidak-terjawab/ringkasan` — count of unanswered questions per status

## Rate Limiting

IP-based (`CF-Connecting-IP` → `X-Forwarded-For` → `request.client.host`, in that order), `DAILY_QUESTION_LIMIT` per identifier per day, reset automatically at midnight since the storage key includes the date. PostgreSQL (`rate_limits` table) is the primary and always-on layer; Cloudflare Workers KV is an optional edge-level cache in front of it. Known limitation: campus NAT means multiple users can share one IP — noted in `api/rate_limiter.py` as a case for SSO + NIM-based identification later.

## Database Schema

```
pengetahuan               → general knowledge base, tagged by unit (biro_1/biro_3/biro_4/pmb)
program_studi              → faculties, majors, degree levels, accreditation
biaya_kuliah                → UKT/tuition by program and degree level
jalur_pendaftaran           → admission tracks (SNBT, Mandiri, Transfer, etc.)
jadwal_pendaftaran          → registration schedules per track
beasiswa                    → scholarships (kategori: mahasiswa_baru/mahasiswa_aktif/eksternal/pinjaman)
pertukaran_mahasiswa        → exchange programs (kategori: outbound/inbound; jenis_program: student_exchange/short_term/iisma)
rate_limits                 → per-identifier daily question counters
pertanyaan_tidak_terjawab   → log of unanswered questions for admin review
```
