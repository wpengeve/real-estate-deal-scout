"""
Real Estate Deal Scout — FastAPI web server.

Usage:
    python app.py                       # development (auto-reload)
    uvicorn app:app --host 0.0.0.0      # production

Endpoints:
    GET  /                        — chat UI
    GET  /history                 — past reports (logged-in users)
    POST /api/auth/request        — request magic link (prints to console)
    GET  /auth/verify             — verify token, set session cookie
    POST /api/logout              — clear session
    GET  /api/me                  — current user or null
    POST /api/chat                — send message, get Claude response
    POST /api/run                 — start pipeline (from chat criteria)
    POST /api/run-form            — start pipeline (from form criteria)
    GET  /api/run/{run_id}        — poll pipeline status
    GET  /reports/{run_id}        — view HTML report
"""
import asyncio
import logging
import os
import uuid
from pathlib import Path

import uvicorn
import yaml
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

# load_dotenv must run before local imports so env vars are available
load_dotenv()

from db import (  # noqa: E402
    create_auth_token,
    create_session,
    delete_session,
    get_db,
    get_or_create_user,
    get_session_user,
    get_user_runs,
    init_db,
    upsert_report_run,
)
from pipeline import run as run_pipeline  # noqa: E402
from tools.models import InvestmentConfig
from tools.web_chat import ChatSession

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

app = FastAPI(title="Real Estate Deal Scout")
templates = Jinja2Templates(directory="templates")

_OUTPUTS_DIR = Path("outputs")
_CONFIG_PATH = Path("config.yaml")

# In-memory pipeline state (resets on restart — reports persist on disk)
_chat_sessions: dict[str, ChatSession] = {}
_runs: dict[str, dict] = {}  # run_id → {status, progress, error}


@app.on_event("startup")
def startup() -> None:
    init_db()
    logger.info("Database initialised")


# ── Dependencies ──────────────────────────────────────────────────────────────

def get_current_user(request: Request, db: Session = Depends(get_db)):
    """FastAPI dependency — returns User or None."""
    session_id = request.cookies.get("scout_session")
    if not session_id:
        return None
    return get_session_user(db, session_id)


def _load_base_config() -> InvestmentConfig:
    with _CONFIG_PATH.open() as f:
        return InvestmentConfig.model_validate(yaml.safe_load(f))


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request, "index.html",
        {"user_email": user.email if user else None},
    )


@app.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/?login=1", status_code=302)
    runs = get_user_runs(db, user.id)
    return templates.TemplateResponse(
        request, "history.html",
        {"user_email": user.email, "runs": runs},
    )


# ── Auth ──────────────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    email: str


@app.post("/api/auth/request")
async def request_magic_link(req: AuthRequest, db: Session = Depends(get_db)):
    email = req.email.lower().strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Invalid email address.")

    user = get_or_create_user(db, email)
    token = create_auth_token(db, user)
    link = f"http://localhost:8000/auth/verify?token={token.token}"

    # No email service yet — print to console for dev/testing
    logger.info("Magic link for %s: %s", email, link)
    print(f"\n🔗 Magic link for {email}:\n   {link}\n")

    return {"message": f"Magic link sent to {email} (check server console for now)"}


@app.get("/auth/verify")
async def verify_magic_link(
    token: str,
    db: Session = Depends(get_db),
):
    from db import verify_auth_token
    user = verify_auth_token(db, token)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired link. Please request a new one.")

    session = create_session(db, user)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        "scout_session",
        session.id,
        max_age=60 * 60 * 24 * 30,  # 30 days
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    session_id = request.cookies.get("scout_session")
    if session_id:
        delete_session(db, session_id)
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("scout_session")
    return response


@app.get("/api/me")
async def me(user=Depends(get_current_user)):
    if not user:
        return {"user": None}
    return {"user": {"email": user.email, "id": user.id}}


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="Chat requires an Anthropic API key. Use the Manual Setup tab to run a scan without one.",
        )
    import anthropic as _anthropic
    if req.session_id not in _chat_sessions:
        _chat_sessions[req.session_id] = ChatSession()
    try:
        return await _chat_sessions[req.session_id].send(req.message)
    except _anthropic.AuthenticationError:
        raise HTTPException(
            status_code=503,
            detail="Anthropic API key is invalid. Check your ANTHROPIC_API_KEY and restart the server.",
        )
    except _anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {e}")


# ── Pipeline runs ─────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    session_id: str


@app.post("/api/run")
async def start_run(
    req: RunRequest,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    session = _chat_sessions.get(req.session_id)
    if not session or not session.extracted:
        raise HTTPException(status_code=400, detail="No confirmed criteria for this session.")
    config = session.build_config(_load_base_config())
    if config is None:
        raise HTTPException(status_code=400, detail="Could not build config from criteria.")
    return _launch_run(config, db, user, criteria=session.extracted)


class FormRunRequest(BaseModel):
    session_id: str
    criteria: dict


@app.post("/api/run-form")
async def start_run_form(
    req: FormRunRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    from tools.chat_intake import _build_config
    config = _build_config(req.criteria, _load_base_config())
    return _launch_run(config, db, user, criteria=req.criteria)


def _launch_run(
    config: InvestmentConfig,
    db: Session,
    user,
    criteria: dict,
) -> dict:
    run_id = uuid.uuid4().hex[:8]
    _runs[run_id] = {"status": "running", "progress": "Starting pipeline..."}
    upsert_report_run(
        db, run_id,
        user_id=user.id if user else None,
        market=config.output.market,
        status="running",
        criteria=criteria,
    )
    asyncio.create_task(_run_pipeline_bg(run_id, config, user_id=user.id if user else None))
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

async def _run_pipeline_bg(
    run_id: str,
    config: InvestmentConfig,
    user_id: int | None = None,
) -> None:
    try:
        _runs[run_id]["progress"] = "Fetching and enriching listings (1–2 min)..."
        shortlist = await run_pipeline(config.output.market, config)

        _OUTPUTS_DIR.mkdir(exist_ok=True)
        report_path = _OUTPUTS_DIR / f"web_{run_id}.html"

        from tools.report import generate_report
        generate_report(shortlist, report_path, config.financial_assumptions)

        _runs[run_id] = {"status": "done", "progress": f"Found {len(shortlist.deals)} deals"}
        logger.info("Run %s complete — %d deals", run_id, len(shortlist.deals))

        # Persist to DB
        db = get_db()
        try:
            upsert_report_run(
                db, run_id,
                user_id=user_id,
                status="done",
                deals_found=len(shortlist.deals),
            )
        finally:
            db.close()

    except (Exception, SystemExit) as e:
        # Catch SystemExit (raised by pipeline when ranker fails) in addition to Exception
        logger.exception("Run %s failed", run_id)
        msg = str(e) or type(e).__name__
        _runs[run_id] = {"status": "error", "progress": msg, "error": msg}
        db = get_db()
        try:
            upsert_report_run(db, run_id, status="error")
        finally:
            db.close()


# ── Dev entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
