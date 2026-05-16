# California Fruit Inc — Sorting Quality Verifier (web app)

A small FastAPI wrapper around `sqr_verifier_v2`. Vicky drops a scanned packet PDF
into the browser, the verifier runs OCR + cross-references every field, and she
gets back a marked-up PDF, an Issues CSV, a cross-reference Excel matrix, and a
JSON audit trail.

No auth, no payment — this is the "does it work" test app. Auth and billing get
added once the verification flow is approved.

## What the app does

1. Browser uploads a packet PDF.
2. Server saves it under `data/jobs/<job_id>/`.
3. A background worker calls `verify_pdf()` from the existing verifier engine.
4. The UI polls job status; when done, it shows pass/fail counts, sub-packets,
   every flag with its detail, and embeds the verified PDF inline.

Outputs land in `data/jobs/<job_id>/out/`:

| File | What it is |
|------|------------|
| `<name>_AI_VERIFIED.pdf`            | Marked-up packet (give to Vicky) |
| `<name>_issues.csv`                 | One row per flag |
| `<name>_cross_reference_matrix.xlsx`| Field × page audit grid |
| `<name>_trace.json`                 | Full structured audit trail |
| `<name>_summary.png`                | Standalone summary image |

## Run locally with Docker (recommended)

```bash
# from this directory
export ANTHROPIC_API_KEY="sk-ant-..."   # required for handwriting OCR
docker compose up --build
```

Open <http://localhost:8000>.

The `data/` folder is mounted as a volume, so jobs persist across restarts.

## Run locally without Docker (Windows, macOS, Linux)

Native runs need Poppler and Tesseract on PATH.

**Windows:**

```powershell
# 1. Install system tools (one-time)
winget install UB-Mannheim.TesseractOCR
winget install oschwartz10612.Poppler

# 2. Install Python deps
pip install -r requirements.txt

# 3. Set the API key
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# 4. Run the server
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

If `winget` doesn't pick the right Poppler, grab the latest release from
<https://github.com/oschwartz10612/poppler-windows/releases> and add its `bin\`
folder to `PATH`.

**macOS:**

```bash
brew install poppler tesseract
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Linux:**

```bash
sudo apt-get install -y poppler-utils tesseract-ocr fonts-dejavu-core
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Deploy to Render

1. Push this folder to a new GitHub repo.
2. In the Render dashboard, click **New → Blueprint**, point it at the repo.
   `render.yaml` provisions a Docker web service with a 5 GB persistent disk
   mounted at `/app/data` for job state.
3. After the first build, open the service's **Environment** tab and set
   `ANTHROPIC_API_KEY` to your key (it's marked `sync: false` so it won't
   leak through the blueprint).
4. Open the service URL. Drop a packet on the page.

The blueprint uses the **starter** plan ($7/mo) because Render's free tier
doesn't support persistent disks — without one, jobs vanish on every restart.
If you don't mind losing job history on redeploy, change `plan: starter` to
`plan: free` and delete the `disk:` block in `render.yaml`.

## Configuration

- `ANTHROPIC_API_KEY` — required for real handwriting OCR. Without it, only
  the "mock" provider works and it only knows the 4 pre-cached sample packets
  (Olive Nation, Mark Keshishian, Balcorp, Pedrick Produce).
- `PORT` — read automatically on Render; defaults to `8000` locally.

## Adding customers or specs

Edit `sqr_verifier_v2/config/customers.yaml` or `sqr_verifier_v2/config/specs.yaml`
and rebuild. No code change needed — the verifier picks up YAML changes on
process start.

## What this app does NOT do yet

These are the deliberately-deferred items the handoff lists; revisit after the
verification flow is approved:

- Authentication / multi-user sign-in
- Per-user digital signatures on the verified PDF
- Cloud-folder auto-archive (Adobe PDF cloud, customer/year/WO structure)
- Edit-and-propagate correction tool (click a flagged field, type corrected
  value, AI sweeps every other page that referenced it)
- Billing / metered usage

## Repo layout

```
webapp/
├── app/
│   ├── main.py              FastAPI: /, /api/upload, /api/jobs, /api/jobs/{id}, ...
│   ├── runner.py            Background-worker wrapper around verify_pdf()
│   └── templates/index.html Drop-zone UI + jobs list + result viewer
├── sqr_verifier_v2/         The existing verifier (copied from CFI_handoff_complete)
├── data/                    Job state + uploads + outputs (gitignored)
├── Dockerfile               python:3.12-slim + Tesseract + Poppler + DejaVu
├── docker-compose.yml       Local dev with ./data volume mount
├── render.yaml              Render blueprint (Docker service + disk)
└── requirements.txt         Python deps
```
