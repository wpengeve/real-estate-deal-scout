











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
    delete_chat_session,
    delete_session,
    get_db,
    get_or_create_user,
    get_session_user,
    get_user_runs,
    init_db,
    load_chat_session,
    save_chat_session,
    upsert_report_run,
)
from pipeline import run as run_pipeline, run_single_property, run_multi_property  # noqa: E402
from tools import config_file  # noqa: E402
from tools.fetch import resolve_address_to_url  # noqa: E402
from tools.models import InvestmentConfig
from tools.web_chat import ChatSession

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

app = FastAPI(title="Real Estate Deal Scout")
templates = Jinja2Templates(directory="templates")
templates.env.auto_reload = True

_DEFAULT_OUTPUTS_DIR = Path("outputs")


def outputs_dir() -> Path:
    """
    Where generated reports are written and served from.

    SCOUT_OUTPUTS_DIR points it at a mounted volume. Reports are the thing a
    shareable /reports/{run_id} URL resolves to, so on a host with ephemeral
    disk every previously shared link 404s after a redeploy.

    Read lazily rather than at import so load_dotenv() ordering can't silently
    send reports to the default directory.
    """
    override = os.getenv("SCOUT_OUTPUTS_DIR")
    return Path(override) if override else _DEFAULT_OUTPUTS_DIR


def _report_path(run_id: str) -> Path:
    """Path a run's report is written to, with the directory ensured."""
    directory = outputs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"web_{run_id}.html"


_CONFIG_PATH = config_file.DEFAULT_CONFIG_PATH

# In-memory pipeline state (resets on restart — reports persist on disk)
_chat_sessions: dict[str, ChatSession] = {}
_runs: dict[str, dict] = {}  # run_id → {status, progress, error}


@app.on_event("startup")
def startup() -> None:
    init_db()
    _prune_outputs(max_age_days=7)
    logger.info("Database initialised")


def _prune_outputs(max_age_days: int = 7) -> None:
    """Delete output files older than max_age_days. Keeps run_log.jsonl."""
    import time
    cutoff = time.time() - max_age_days * 86400
    pruned = 0
    for f in outputs_dir().glob("*"):
        if f.name == "run_log.jsonl" or not f.is_file():
            continue
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                pruned += 1
            except OSError:
                pass
    if pruned:
        logger.info("Pruned %d output file(s) older than %d days", pruned, max_age_days)


# ── Dependencies ──────────────────────────────────────────────────────────────

def get_current_user(request: Request, db: Session = Depends(get_db)):
    """FastAPI dependency — returns User or None."""
    session_id = request.cookies.get("scout_session")
    if not session_id:
        return None
    return get_session_user(db, session_id)


def _load_base_config() -> InvestmentConfig:
    return config_file.load_config(_CONFIG_PATH)


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
    base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    link = f"{base_url}/auth/verify?token={token.token}"

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


def _extract_addresses(text: str) -> list[str]:
    """
    Extract multiple addresses from a message, ignoring any surrounding text.
    Handles newline-separated and inline comma-separated formats.
    A line is an address if it starts with a house number followed by a street name.
    """
    import re
    _addr_re = re.compile(
        r"^\d+\s+\w.*\b(street|avenue|boulevard|road|drive|lane|way|place|court|"
        r"circle|terrace|highway|parkway|square|st|ave|blvd|rd|dr|ln|pl|ct|cir|"
        r"ter|hwy|pkwy|sq)\b",
        re.I,
    )
    # Filter only lines that look like addresses
    lines = [l.strip().rstrip(",").strip() for l in text.splitlines() if l.strip()]
    addr_lines = [l for l in lines if _addr_re.match(l)]
    if len(addr_lines) >= 2:
        return addr_lines
    # Fallback: try splitting entire text on comma boundaries before house numbers
    parts = re.split(r",\s*(?=\d+\s+\w)", text)
    parts = [p.strip().rstrip(",").strip() for p in parts if p.strip()]
    addr_parts = [p for p in parts if _addr_re.match(p)]
    if len(addr_parts) >= 2:
        return addr_parts
    return []


@app.post("/api/chat")
async def chat(req: ChatRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # Intercept multi-address messages before sending to Claude
    addresses = _extract_addresses(req.message)
    if len(addresses) >= 2:
        session = _chat_sessions.get(req.session_id)
        if session and session.extracted:
            config = session.build_config(_load_base_config())
        else:
            saved = load_chat_session(db, req.session_id)
            if saved and saved[1]:
                from tools.chat_intake import _build_config
                config = _build_config(saved[1], _load_base_config())
            else:
                config = _load_base_config()

        run_id = uuid.uuid4().hex[:8]
        _runs[run_id] = {"status": "running", "progress": f"Resolving {len(addresses)} addresses..."}
        upsert_report_run(db, run_id, user_id=user.id if user else None,
                          market=config.output.market, status="running",
                          criteria={"addresses": addresses})
        asyncio.create_task(_run_multi_property_bg(run_id, addresses, config,
                                                    user_id=user.id if user else None,
                                                    session_id=req.session_id))
        return {"text": f"🔍 Resolving {len(addresses)} addresses and comparing...", "run_id": run_id}

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="Chat requires an Anthropic API key. Use the Manual Setup tab to run a scan without one.",
        )
    import anthropic as _anthropic

    # Load session from DB if not already in memory
    if req.session_id not in _chat_sessions:
        db = get_db()
        try:
            saved = load_chat_session(db, req.session_id)
        finally:
            db.close()
        if saved:
            _chat_sessions[req.session_id] = ChatSession(messages=saved[0], extracted=saved[1])
        else:
            _chat_sessions[req.session_id] = ChatSession()

    session = _chat_sessions[req.session_id]
    try:
        result = await session.send(req.message)
    except _anthropic.AuthenticationError:
        raise HTTPException(
            status_code=503,
            detail="Anthropic API key is invalid. Check your ANTHROPIC_API_KEY and restart the server.",
        )
    except _anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {e}")

    # Persist updated state to DB
    db = get_db()
    try:
        save_chat_session(db, req.session_id, session.messages, session.extracted)
    finally:
        db.close()

    return result


@app.get("/api/chat/history")
async def chat_history(session_id: str):
    """Return persisted chat history for a session so the UI can restore it on page load."""
    # Check in-memory cache first
    session = _chat_sessions.get(session_id)
    if session:
        return {"messages": _visible_messages(session.messages), "criteria": session.extracted}

    db = get_db()
    try:
        saved = load_chat_session(db, session_id)
    finally:
        db.close()

    if not saved:
        return {"messages": [], "criteria": None}
    return {"messages": _visible_messages(saved[0]), "criteria": saved[1]}


def _visible_messages(messages: list[dict]) -> list[dict]:
    """Return only user/assistant text turns — strip tool_result plumbing."""
    visible = []
    for msg in messages:
        if msg["role"] == "user":
            content = msg["content"]
            # Skip tool_result messages (criteria acknowledgement plumbing)
            if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                continue
            visible.append({"role": "user", "text": content if isinstance(content, str) else ""})
        elif msg["role"] == "assistant":
            content = msg["content"]
            texts = []
            if isinstance(content, list):
                for block in content:
                    t = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                    if t == "text":
                        texts.append(block["text"] if isinstance(block, dict) else block.text)
            if texts:
                visible.append({"role": "assistant", "text": "\n".join(texts)})
    return visible


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
    return _launch_run(config, db, user, criteria=session.extracted, session_id=req.session_id)


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
    return _launch_run(config, db, user, criteria=req.criteria, session_id=req.session_id)


def _launch_run(
    config: InvestmentConfig,
    db: Session,
    user,
    criteria: dict,
    session_id: str | None = None,
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
    asyncio.create_task(_run_pipeline_bg(run_id, config, user_id=user.id if user else None, session_id=session_id))
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
        "preview_deals": run.get("preview_deals", []),
        "all_addresses": run.get("all_addresses", []),
        "purpose": run.get("purpose", "rental"),
    }


@app.get("/reports/{run_id}", response_class=FileResponse)
async def view_report(run_id: str):
    report_path = outputs_dir() / f"web_{run_id}.html"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(report_path, media_type="text/html")


# ── Address resolution ────────────────────────────────────────────────────────

class ResolveAddressRequest(BaseModel):
    address: str


@app.post("/api/resolve-address")
async def resolve_address(req: ResolveAddressRequest):
    """Resolve a plain address to a Redfin listing URL."""
    url = await resolve_address_to_url(req.address)
    return {"url": url}


# ── Single-property analysis ──────────────────────────────────────────────────

class AnalyzePropertyRequest(BaseModel):
    session_id: str
    url: str


@app.post("/api/analyze-property")
async def analyze_property(
    req: AnalyzePropertyRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Analyze a single Redfin listing URL using the session's financial criteria."""
    if not req.url or "redfin.com" not in req.url:
        raise HTTPException(status_code=422, detail="Please provide a valid Redfin listing URL.")

    # Use session criteria if available, otherwise fall back to base config
    session = _chat_sessions.get(req.session_id)
    if session and session.extracted:
        config = session.build_config(_load_base_config())
    else:
        # Try loading from DB
        saved = load_chat_session(db, req.session_id)
        if saved and saved[1]:
            from tools.chat_intake import _build_config
            config = _build_config(saved[1], _load_base_config())
        else:
            config = _load_base_config()

    run_id = uuid.uuid4().hex[:8]
    _runs[run_id] = {"status": "running", "progress": "Fetching listing..."}
    upsert_report_run(
        db, run_id,
        user_id=user.id if user else None,
        market=req.url,
        status="running",
        criteria={"url": req.url},
    )
    asyncio.create_task(_run_single_property_bg(run_id, req.url, config, session_id=req.session_id, user_id=user.id if user else None))
    return {"run_id": run_id}


async def _run_single_property_bg(
    run_id: str,
    url: str,
    config: InvestmentConfig,
    session_id: str | None = None,
    user_id: int | None = None,
) -> None:
    try:
        def _cb(msg: str) -> None:
            _runs[run_id]["progress"] = msg

        _runs[run_id]["progress"] = "[1/3] Enriching listing with market data..."
        shortlist = await run_single_property(url, config, progress_cb=_cb)

        report_path = _report_path(run_id)

        from tools.report import generate_report
        generate_report(shortlist, report_path, config.financial_assumptions)

        preview_deals = [
            {
                "address": d.address,
                "price": d.price,
                "cap_rate": d.cap_rate,
                "monthly_cashflow": d.monthly_cashflow,
                "monthly_piti": d.monthly_piti,
            }
            for d in shortlist.deals[:3]
        ]
        all_addresses = [d.address for d in shortlist.deals if d.address]
        _runs[run_id] = {
            "status": "done",
            "progress": "Analysis complete",
            "preview_deals": preview_deals,
            "all_addresses": all_addresses,
            "purpose": shortlist.purpose,
        }

        if session_id and session_id in _chat_sessions:
            _chat_sessions[session_id].set_shortlist(shortlist)

        db = get_db()
        try:
            upsert_report_run(db, run_id, user_id=user_id, status="done", deals_found=len(shortlist.deals))
        finally:
            db.close()

    except Exception as e:
        logger.exception("Single-property analysis %s failed", run_id)
        msg = str(e) or type(e).__name__
        _runs[run_id] = {"status": "error", "progress": msg, "error": msg}
        db = get_db()
        try:
            upsert_report_run(db, run_id, status="error")
        finally:
            db.close()


# ── Multi-property analysis ───────────────────────────────────────────────────

class AnalyzeMultiRequest(BaseModel):
    session_id: str
    addresses: list[str]


@app.post("/api/analyze-multi")
async def analyze_multi(
    req: AnalyzeMultiRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Resolve multiple addresses → fetch listings → ranked comparison report."""
    if not req.addresses or len(req.addresses) < 2:
        raise HTTPException(status_code=422, detail="Please provide at least 2 addresses.")
    if len(req.addresses) > 10:
        raise HTTPException(status_code=422, detail="Maximum 10 addresses at once.")

    session = _chat_sessions.get(req.session_id)
    if session and session.extracted:
        config = session.build_config(_load_base_config())
    else:
        saved = load_chat_session(db, req.session_id)
        if saved and saved[1]:
            from tools.chat_intake import _build_config
            config = _build_config(saved[1], _load_base_config())
        else:
            config = _load_base_config()

    run_id = uuid.uuid4().hex[:8]
    _runs[run_id] = {"status": "running", "progress": f"Resolving {len(req.addresses)} addresses..."}
    upsert_report_run(
        db, run_id,
        user_id=user.id if user else None,
        market=config.output.market,
        status="running",
        criteria={"addresses": req.addresses},
    )
    asyncio.create_task(_run_multi_property_bg(run_id, req.addresses, config, user_id=user.id if user else None))
    return {"run_id": run_id}


async def _run_multi_property_bg(
    run_id: str,
    addresses: list[str],
    config: InvestmentConfig,
    user_id: int | None = None,
    session_id: str | None = None,
) -> None:
    try:
        _runs[run_id]["progress"] = f"Resolving {len(addresses)} addresses concurrently..."
        resolve_results = await asyncio.gather(
            *[resolve_address_to_url(addr) for addr in addresses],
            return_exceptions=True,
        )

        urls = []
        failed = []
        for address, result in zip(addresses, resolve_results):
            if isinstance(result, Exception) or not result:
                logger.warning("Could not resolve address: %s", address)
                failed.append(address.split(",")[0])  # short label for progress
            else:
                urls.append(result)

        if not urls:
            raise ValueError("Could not resolve any of the provided addresses to Redfin listings.")

        if failed:
            _runs[run_id]["progress"] = (
                f"Resolved {len(urls)}/{len(addresses)} addresses "
                f"(skipped: {', '.join(failed)}). Analyzing..."
            )
        else:
            _runs[run_id]["progress"] = f"Analyzing {len(urls)} properties..."
        shortlist = await run_multi_property(urls, config)

        report_path = _report_path(run_id)

        from tools.report import generate_report
        generate_report(shortlist, report_path, config.financial_assumptions)

        all_addresses = [d.address for d in shortlist.deals if d.address]
        _runs[run_id] = {
            "status": "done",
            "progress": f"Compared {len(shortlist.deals)} properties",
            "all_addresses": all_addresses,
            "purpose": shortlist.purpose,
        }

        if session_id and session_id in _chat_sessions:
            _chat_sessions[session_id].set_shortlist(shortlist)

        db = get_db()
        try:
            upsert_report_run(db, run_id, user_id=user_id, status="done", deals_found=len(shortlist.deals))
        finally:
            db.close()

    except Exception as e:
        logger.exception("Multi-property analysis %s failed", run_id)
        msg = str(e) or type(e).__name__
        _runs[run_id] = {"status": "error", "progress": msg, "error": msg}
        db = get_db()
        try:
            upsert_report_run(db, run_id, status="error")
        finally:
            db.close()


# ── Background pipeline task ──────────────────────────────────────────────────

async def _run_pipeline_bg(
    run_id: str,
    config: InvestmentConfig,
    user_id: int | None = None,
    session_id: str | None = None,
) -> None:
    try:
        def _cb(msg: str) -> None:
            _runs[run_id]["progress"] = msg

        _runs[run_id]["progress"] = "[1/6] Fetching listings from Redfin..."
        shortlist = await run_pipeline(config.output.market, config, progress_cb=_cb)

        report_path = _report_path(run_id)

        from tools.report import generate_report
        generate_report(shortlist, report_path, config.financial_assumptions)

        preview_deals = [
            {
                "address": d.address,
                "price": d.price,
                "cap_rate": d.cap_rate,
                "monthly_cashflow": d.monthly_cashflow,
                "monthly_piti": d.monthly_piti,
            }
            for d in shortlist.deals[:3]
        ]
        all_addresses = [d.address for d in shortlist.deals if d.address]
        _runs[run_id] = {
            "status": "done",
            "progress": f"Found {len(shortlist.deals)} deals",
            "preview_deals": preview_deals,
            "all_addresses": all_addresses,
            "purpose": shortlist.purpose,
        }
        logger.info("Run %s complete — %d deals", run_id, len(shortlist.deals))

        if session_id and session_id in _chat_sessions:
            _chat_sessions[session_id].set_shortlist(shortlist)

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
