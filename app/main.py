"""
California Fruit Inc — Sorting-Quality Verifier web UI.

Endpoints:
  GET  /                           HTML drop-zone + jobs list
  POST /api/upload                 multipart PDF upload -> job_id
  GET  /api/jobs                   list all jobs (newest first)
  GET  /api/jobs/{id}              single job record
  GET  /api/jobs/{id}/files/{rel}  serve any artifact inside the job's dir
  GET  /healthz                    liveness check
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import runner


APP_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(APP_ROOT / "templates"))

# Belt-and-suspenders: ensure the static dir exists before mounting so the
# server doesn't crash at boot on ephemeral filesystems.
STATIC_DIR = APP_ROOT / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="California Fruit Inc — Sorting Quality Verifier")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/healthz")
def healthz():
    return {"ok": True, "has_anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return TEMPLATES.TemplateResponse("index.html", {
        "request": request,
        "has_anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
    })


@app.post("/api/upload")
async def upload(file: UploadFile = File(...),
                 ocr_provider: str = Form("anthropic"),
                 use_cache: str = Form("true")):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file was empty.")
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "PDF too large (200 MB max).")

    if ocr_provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            400,
            "ANTHROPIC_API_KEY not set on the server. Either set it (recommended) "
            "or choose the 'mock' provider (only works for the 4 pre-cached sample packets)."
        )

    job = runner.submit_job(
        pdf_bytes=data,
        original_filename=file.filename,
        ocr_provider=ocr_provider,
        use_cache=(use_cache.lower() == "true"),
    )
    return {"job_id": job.id, "status": job.status, "filename": job.filename}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [j.to_dict() for j in runner.list_jobs()]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = runner.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    return j.to_dict()


@app.get("/api/jobs/{job_id}/files/{rel:path}")
def get_file(job_id: str, rel: str):
    j = runner.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    base = runner.job_dir(job_id).resolve()
    target = (base / rel).resolve()
    # Path traversal guard
    if base not in target.parents and target != base:
        raise HTTPException(400, "Invalid path")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found")

    # Set inline disposition for PDFs/PNGs so the browser previews them
    media = None
    name_lower = target.name.lower()
    if name_lower.endswith(".pdf"):
        media = "application/pdf"
    elif name_lower.endswith(".png"):
        media = "image/png"
    elif name_lower.endswith(".csv"):
        media = "text/csv"
    elif name_lower.endswith(".json"):
        media = "application/json"
    elif name_lower.endswith(".xlsx"):
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(str(target), media_type=media, filename=target.name
                        if name_lower.endswith((".csv", ".xlsx", ".json")) else None)
