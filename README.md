# Hackstrom Track 3

Intelligent document processing platform with multi-country compliance rules, LLM-based field extraction, and a real-time workflow visualization UI.

---

## Architecture Overview

```
hackstrom-track3/
├── backend/              # FastAPI service
│   ├── main.py           # App factory, CORS, router registration
│   ├── models.py         # Pydantic v2 schemas (Document, Workflow, Extraction, Validation)
│   ├── graph.py          # LangGraph pipeline (ingest → extract → validate → store)
│   └── routes/
│       ├── upload.py     # POST /api/v1/upload/  — accept PDF/image, store to disk
│       └── workflow.py   # POST /api/v1/workflow/ — trigger & poll LangGraph pipeline
├── frontend/             # React 18 + Vite SPA
│   └── src/
│       ├── pages/
│       │   ├── UploadPage.tsx    # Drag-and-drop upload form (react-hook-form + zod)
│       │   └── WorkflowPage.tsx  # Live pipeline graph (@xyflow/react)
│       ├── components/ui/        # shadcn/ui primitives (Button, Card, Badge)
│       └── lib/
│           ├── api.ts            # Typed axios wrappers + TS mirrors of backend models
│           └── utils.ts          # cn() helper (clsx + tailwind-merge)
└── country_rules/
    ├── us.yaml           # US KYC/AML field requirements (SSN/EIN, OFAC, BSA 7-year retention)
    └── uae.yaml          # UAE KYC/AML rules (Emirates ID, CBUAE, 5-year retention)
```

---

## Data Flow

```
Browser
  │
  │ multipart/form-data (PDF/image + country)
  ▼
POST /api/v1/upload/
  │  saves file to disk, creates DocumentRecord (pending)
  ▼
POST /api/v1/workflow/
  │  spawns BackgroundTask → LangGraph pipeline
  ▼
  ┌──────────────────────────────────────────────────────┐
  │  LangGraph Document Pipeline (graph.py)              │
  │                                                      │
  │  ingest ──► extract ──► validate ──► store ──► END   │
  │                                                      │
  │  ingest   : Docling / PyMuPDF → raw_text + images    │
  │  extract  : Instructor + LLM → structured fields     │
  │  validate : country_rules/<country>.yaml checks      │
  │  store    : ChromaDB (embeddings) + SQLModel (DB)    │
  └──────────────────────────────────────────────────────┘
  │
  ▼
GET /api/v1/workflow/{id}   ← polled every 3 s by React Query
  │
  ▼
WorkflowPage (ReactFlow graph, nodes colour green when complete)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI + Uvicorn |
| Data validation | Pydantic v2 |
| AI orchestration | LangGraph |
| LLM structured output | Instructor |
| Document parsing | Docling, PyMuPDF, Pillow, OpenCV |
| Vector store | ChromaDB + sentence-transformers |
| SQL ORM | SQLModel |
| HTTP client | HTTPX + Tenacity (retries) |
| Observability | structlog, OpenTelemetry SDK, Arize Phoenix |
| Frontend | React 18, TypeScript, Vite |
| Styling | Tailwind CSS + shadcn/ui |
| State management | TanStack Query v5 |
| Forms | react-hook-form + Zod |
| PDF viewer | react-pdf |
| Pipeline graph | @xyflow/react |

---

## Getting Started

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
# API at http://localhost:8000 — docs at http://localhost:8000/docs
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# UI at http://localhost:5173
```

### Country Rules

YAML files in `country_rules/` are loaded at runtime by the `validate` node in `graph.py`. Add a new file (e.g. `uk.yaml`) and extend the `CountryCode` enum in `models.py` to support additional jurisdictions.

---

## Environment Variables (backend)

| Variable | Purpose | Example |
|---|---|---|
| `OPENAI_API_KEY` | LLM calls via Instructor | `sk-...` |
| `DATABASE_URL` | SQLModel connection string | `sqlite:///./dev.db` |
| `CHROMA_HOST` | ChromaDB host | `localhost` |
| `CHROMA_PORT` | ChromaDB port | `8001` |
| `PHOENIX_ENDPOINT` | Arize Phoenix collector | `http://localhost:6006` |

Copy `.env.example` (create one) and fill in values before running.
