"""
Thin wrapper around sqr_verifier_v2.src.verifier:
  - Adds the verifier's src/ to sys.path so the legacy import works
  - Runs verify_pdf in a background thread
  - Tracks job state in an in-memory dict, persisted to a status.json on disk
    so the UI can recover after a container restart
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the existing verifier importable as a top-level module set
APP_ROOT = Path(__file__).resolve().parent.parent
VERIFIER_SRC = APP_ROOT / "sqr_verifier_v2" / "src"
VERIFIER_CONFIG = APP_ROOT / "sqr_verifier_v2" / "config"
VERIFIER_CACHE = APP_ROOT / "sqr_verifier_v2" / "cache"

if str(VERIFIER_SRC) not in sys.path:
    sys.path.insert(0, str(VERIFIER_SRC))

# Data lives in /app/data inside container; mounted to ./data on host
DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
JOBS_ROOT = DATA_ROOT / "jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class JobRecord:
    id: str
    filename: str
    status: str  # queued | running | done | failed
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None  # tallies, sub-packet list, etc.
    artifacts: Dict[str, str] = field(default_factory=dict)
    log: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -------- in-memory state + lock --------

_jobs: Dict[str, JobRecord] = {}
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1)  # serialize: heavy CPU + memory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save_status(job: JobRecord) -> None:
    p = JOBS_ROOT / job.id / "status.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(job.to_dict(), indent=2))


def _load_existing_jobs() -> None:
    if not JOBS_ROOT.exists():
        return
    for d in JOBS_ROOT.iterdir():
        if not d.is_dir():
            continue
        sp = d / "status.json"
        if not sp.exists():
            continue
        try:
            data = json.loads(sp.read_text())
            job = JobRecord(**{k: v for k, v in data.items() if k in JobRecord.__dataclass_fields__})
            # If the container was killed mid-run, mark as failed
            if job.status in ("queued", "running"):
                job.status = "failed"
                job.error = (job.error or "") + " (container restarted before completion)"
                _save_status(job)
            _jobs[job.id] = job
        except Exception:
            pass


_load_existing_jobs()


# -------- the actual verification call --------

def _run_verifier(job_id: str, pdf_path: Path, ocr_provider: str,
                  use_cache: bool) -> None:
    """Runs in worker thread. Updates job state."""
    job = _jobs[job_id]
    job.status = "running"
    job.started_at = _now()
    job.log.append(f"[{_now()}] starting verifier (provider={ocr_provider})")
    _save_status(job)

    try:
        # late import so sys.path tweak above is in effect
        from verifier import verify_pdf  # type: ignore

        out_dir = JOBS_ROOT / job_id / "out"
        out_dir.mkdir(parents=True, exist_ok=True)

        vision_cache = str(VERIFIER_CACHE / "vision_cache.json") if use_cache else None

        report = verify_pdf(
            pdf_path=str(pdf_path),
            out_dir=str(out_dir),
            config_dir=str(VERIFIER_CONFIG),
            ocr_provider=ocr_provider,
            vision_cache_path=vision_cache,
            packet_name=pdf_path.stem,
        )

        # Discover artifacts
        artifacts = {}
        name = pdf_path.stem
        for label, suffix in [
            ("Verified PDF",         f"{name}_AI_VERIFIED.pdf"),
            ("Summary PNG",          f"{name}_summary.png"),
            ("Issues CSV",           f"{name}_issues.csv"),
            ("Trace JSON",           f"{name}_trace.json"),
            ("Cross-ref matrix",     f"{name}_cross_reference_matrix.xlsx"),
        ]:
            p = out_dir / suffix
            if p.exists():
                artifacts[label] = f"out/{p.name}"

        # Build summary payload for the UI
        sub_packets = []
        for sp in report.sub_packets:
            sub_packets.append({
                "index": sp.index + 1,
                "wo": sp.primary_wo,
                "po": sp.primary_po,
                "customer": sp.primary_customer,
                "product": sp.primary_product,
                "cases": sp.cases,
                "pages": [p.page_no for p in sp.pages],
            })

        flagged = [
            {
                "name": c.name,
                "status": c.status,
                "detail": c.detail,
                "pages": c.pages,
                "sub_packet": (c.sub_packet + 1) if c.sub_packet is not None else None,
            }
            for c in report.all_checks if c.status in ("fail", "info")
        ]

        n_vision = sum(1 for p in report.pages if p.ocr_backend_used == "vision")
        job.summary = {
            "overall": report.overall,
            "pages": len(report.pages),
            "vision_ocr_pages": n_vision,
            "tesseract_only_pages": len(report.pages) - n_vision,
            "sub_packets": sub_packets,
            "tally": {"pass": report.n_pass, "fail": report.n_fail, "info": report.n_info},
            "customer": report.customer_profile.canonical if report.customer_profile else None,
            "flagged": flagged[:200],
        }
        job.artifacts = artifacts
        job.status = "done"
        job.finished_at = _now()
        job.log.append(f"[{_now()}] done — {report.overall}, "
                       f"{report.n_pass} pass / {report.n_fail} fail / {report.n_info} info")
        _save_status(job)
    except Exception as e:
        job.status = "failed"
        job.finished_at = _now()
        job.error = f"{type(e).__name__}: {e}"
        job.log.append(f"[{_now()}] ERROR: {job.error}")
        job.log.append(traceback.format_exc())
        _save_status(job)


def submit_job(pdf_bytes: bytes, original_filename: str,
               ocr_provider: str = "anthropic",
               use_cache: bool = True) -> JobRecord:
    """Save the uploaded PDF, register a job, and dispatch verification."""
    job_id = uuid.uuid4().hex[:12]
    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(original_filename).name
    pdf_path = job_dir / safe_name
    pdf_path.write_bytes(pdf_bytes)

    job = JobRecord(
        id=job_id,
        filename=safe_name,
        status="queued",
        created_at=_now(),
    )
    with _jobs_lock:
        _jobs[job_id] = job
    _save_status(job)

    _executor.submit(_run_verifier, job_id, pdf_path, ocr_provider, use_cache)
    return job


def get_job(job_id: str) -> Optional[JobRecord]:
    return _jobs.get(job_id)


def list_jobs() -> List[JobRecord]:
    # Newest first
    return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)


def job_dir(job_id: str) -> Path:
    return JOBS_ROOT / job_id
