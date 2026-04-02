"""
Real Estate Deal Scout — FastAPI web server.

Usage:
    python app.py                       # development (auto-reload)
    uvicorn app:app --host 0.0.0.0      # production

Endpoints:
    GET  /                    — chat UI
    POST /api/chat            — send message, get Claude response + extracted criteria
    POST /api/run             — start pipeline with confirmed criteria
    GET  /api/run/{run_id}    — poll pipeline status
    GET  /reports/{run_id}    — view completed HTML report
"""
import asyncio
import logging
import os
import uuid
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# load_dotenv must run before local imports so env vars are available
load_dotenv()

from pipeline import run as run_pipeline  # noqa: E402
from tools.models import InvestmentConfig
from tools.web_chat import ChatSession

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

app = FastAPI(title="Real Estate Deal Scout")
templates = Jinja2Templates(directory="templates")

_OUTPUTS_DIR = Path("outputs")
_CONFIG_PATH = Path("config.yaml")

# In-memory state — reset on server restart (MVP)
_sessions: dict[str, ChatSession] = {}
_runs: dict[str, dict] = {}  # run_id → {status, progress, error}


def _load_base_config() -> InvestmentConfig:
    with _CONFIG_PATH.open() as f:
        return InvestmentConfig.model_validate(yaml.safe_load(f))


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not configured on the server.",
        )

    if req.session_id not in _sessions:
        _sessions[req.session_id] = ChatSession()

    result = await _sessions[req.session_id].send(req.message)
    return result


class RunRequest(BaseModel):
    session_id: str


@app.post("/api/run")
async def start_run(req: RunRequest):
    session = _sessions.get(req.session_id)
    if not session or not session.extracted:
        raise HTTPException(status_code=400, detail="No confirmed criteria for this session.")

    config = session.build_config(_load_base_config())
    if config is None:
        raise HTTPException(status_code=400, detail="Could not build config from criteria.")

    run_id = uuid.uuid4().hex[:8]
    _runs[run_id] = {"status": "running", "progress": "Starting pipeline..."}
    asyncio.create_task(_run_pipeline_bg(run_id, config))
    return {"run_id": run_id}


@app.get("/api/run/{run_id}")
async def run_status(run_id: str):
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return {
        "status": run["status"],
        "progress": run.get("progress", ""),
        "report_url": f"/reports/{run_id}" if run["status"] == "done" else None,
        "error": run.get("error"),
    }


@app.get("/reports/{run_id}", response_class=FileResponse)
async def view_report(run_id: str):
    report_path = _OUTPUTS_DIR / f"web_{run_id}.html"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(report_path, media_type="text/html")


# ── Background pipeline task ──────────────────────────────────────────────────

async def _run_pipeline_bg(run_id: str, config: InvestmentConfig) -> None:
    try:
        _runs[run_id]["progress"] = "Fetching and enriching listings (1–2 min)..."
        shortlist = await run_pipeline(config.output.market, config)

        _OUTPUTS_DIR.mkdir(exist_ok=True)
        report_path = _OUTPUTS_DIR / f"web_{run_id}.html"

        from tools.report import generate_report
        generate_report(shortlist, report_path, config.financial_assumptions)

        _runs[run_id] = {
            "status": "done",
            "progress": f"Found {len(shortlist.deals)} deals",
        }
        logger.info("Run %s complete — %d deals", run_id, len(shortlist.deals))

    except Exception as e:
        logger.exception("Run %s failed", run_id)
        _runs[run_id] = {"status": "error", "progress": str(e), "error": str(e)}


# ── Dev entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)