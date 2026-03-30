"""
CHIMERA Data Processor — Sessions
==================================
FastAPI backend for intelligent data retrieval, analytics,
and pipeline management for Lay Engine session data.

Deployed on Google Cloud Run (europe-west2).
Frontend served from Cloudflare Pages.
"""

import os
import io
import json
import logging
from pathlib import Path
from datetime import date, datetime, timezone
from typing import Optional

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Query,
    BackgroundTasks,
    HTTPException,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel
from contextlib import asynccontextmanager

from db import init_db, get_pool
from lay_engine_client import LayEngineClient
from scheduler import (
    start_scheduler,
    stop_scheduler,
    get_scheduler_status,
    configure_scheduler,
)

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("main")

# ── Config ───────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_MINUTES", "15"))

# ── Lazy AI clients ──────────────────────────────────────────
_anthropic_client = None
_openai_client = None


def get_anthropic():
    global _anthropic_client
    if _anthropic_client is None and ANTHROPIC_API_KEY:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


def get_openai():
    global _openai_client
    if _openai_client is None and OPENAI_API_KEY:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


# ── Lay Engine client ────────────────────────────────────────
lay_engine = LayEngineClient(
    base_url=os.environ.get(
        "LAY_ENGINE_URL",
        "https://chimera-flumine-950990732577.europe-west2.run.app",
    ),
    api_key=os.environ.get("LAY_ENGINE_API_KEY", ""),
)

# ── Rules description (for AI prompts) ──────────────────────
RULES_DESCRIPTION = """
CHIMERA Lay Engine Rules (UK_IE_Favourite_Lay v2.0):
- RULE_1: Favourite odds < 2.0 → LAY favourite @ £3
- RULE_2: Favourite odds 2.0–5.0 → LAY favourite @ £2
- RULE_3A: Favourite odds > 5.0 AND gap to 2nd fav < 2 → LAY favourite @ £1 + LAY 2nd fav @ £1
- RULE_3B: Favourite odds > 5.0 AND gap to 2nd fav ≥ 2 → LAY favourite @ £1
Markets: Horse Racing WIN markets. Countries: GB, IE, ZA, FR.
Max odds threshold: 50.0. Timing: pre-off. Poll interval: 30s.
"""


# ── App lifespan ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    await init_db()
    start_scheduler(poll_interval_minutes=POLL_INTERVAL, enabled=True)
    log.info("CHIMERA Data Processor started")
    yield
    stop_scheduler()
    pool = get_pool()
    if pool:
        await pool.close()
    log.info("CHIMERA Data Processor stopped")


app = FastAPI(
    title="CHIMERA Data Processor — Sessions",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        FRONTEND_URL,
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models ─────────────────────────────────────────
class SyncRequest(BaseModel):
    date: Optional[str] = None


class SchedulerConfig(BaseModel):
    poll_interval_minutes: int = 15
    enabled: bool = True


class ReportRequest(BaseModel):
    date: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    date: Optional[str] = None


class KnowledgeEntry(BaseModel):
    category: str
    content: str
    date_relevant: Optional[str] = None


# ══════════════════════════════════════════════════════════════
# DATA SYNC LOGIC
# ══════════════════════════════════════════════════════════════

async def sync_date_data(target_date: str) -> dict:
    """Pull sessions/bets/results from Lay Engine for a date, upsert into DB."""
    pool = get_pool()
    data = await lay_engine.get_sessions(date=target_date)
    sessions = data.get("sessions", [])

    synced_sessions = 0
    synced_bets = 0
    synced_results = 0

    for s in sessions:
        summary = s.get("summary", {})

        # Upsert session
        await pool.execute(
            """
            INSERT INTO sessions (
                session_id, mode, date, start_time, stop_time, status,
                total_bets, total_stake, total_liability, markets_processed,
                raw_json, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW())
            ON CONFLICT (session_id) DO UPDATE SET
                status = EXCLUDED.status,
                stop_time = EXCLUDED.stop_time,
                total_bets = EXCLUDED.total_bets,
                total_stake = EXCLUDED.total_stake,
                total_liability = EXCLUDED.total_liability,
                markets_processed = EXCLUDED.markets_processed,
                raw_json = EXCLUDED.raw_json,
                updated_at = NOW()
            """,
            s["session_id"],
            s["mode"],
            s["date"],
            s["start_time"],
            s.get("stop_time"),
            s["status"],
            summary.get("total_bets", 0),
            summary.get("total_stake", 0),
            summary.get("total_liability", 0),
            summary.get("markets_processed", 0),
            json.dumps(s),
        )
        synced_sessions += 1

        # Upsert bets — delete existing for this session, re-insert
        await pool.execute(
            "DELETE FROM bets WHERE session_id = $1", s["session_id"]
        )
        for b in s.get("bets", []):
            bf = b.get("betfair_response", {})
            await pool.execute(
                """
                INSERT INTO bets (
                    session_id, market_id, selection_id, runner_name,
                    price, size, liability, rule_applied, venue, country,
                    bet_timestamp, dry_run, betfair_status, betfair_bet_id,
                    raw_json
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                """,
                s["session_id"],
                b.get("market_id"),
                b.get("selection_id"),
                b.get("runner_name"),
                b.get("price"),
                b.get("size"),
                b.get("liability"),
                b.get("rule_applied"),
                b.get("venue"),
                b.get("country"),
                b.get("timestamp"),
                b.get("dry_run", True),
                bf.get("status"),
                bf.get("bet_id"),
                json.dumps(b),
            )
            synced_bets += 1

        # Upsert results — same delete + re-insert approach
        await pool.execute(
            "DELETE FROM results WHERE session_id = $1", s["session_id"]
        )
        for r in s.get("results", []):
            fav = r.get("favourite", {})
            sec = r.get("second_favourite", {})
            await pool.execute(
                """
                INSERT INTO results (
                    session_id, market_id, market_name, venue, race_time,
                    favourite_name, favourite_odds, favourite_selection,
                    second_fav_name, second_fav_odds, second_fav_selection,
                    skipped, skip_reason, rule_applied, evaluated_at,
                    total_stake, total_liability, instruction_count, raw_json
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
                """,
                s["session_id"],
                r.get("market_id"),
                r.get("market_name"),
                r.get("venue"),
                r.get("race_time"),
                fav.get("name"),
                fav.get("odds"),
                fav.get("selection_id"),
                sec.get("name"),
                sec.get("odds"),
                sec.get("selection_id"),
                r.get("skipped", False),
                r.get("skip_reason", ""),
                r.get("rule_applied"),
                r.get("evaluated_at"),
                r.get("total_stake"),
                r.get("total_liability"),
                len(r.get("instructions", [])),
                json.dumps(r),
            )
            synced_results += 1

    return {"sessions": synced_sessions, "bets": synced_bets, "results": synced_results}


# ══════════════════════════════════════════════════════════════
# HEALTH & SYNC ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.get("/api/health")
async def health():
    pool = get_pool()
    db_ok = False
    if pool:
        try:
            await pool.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            pass
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "scheduler": get_scheduler_status(),
    }


@app.post("/api/sync")
async def sync_data(req: SyncRequest):
    target = req.date or date.today().isoformat()
    try:
        stats = await sync_date_data(target)
        return {"status": "ok", "date": target, "synced": stats}
    except Exception as e:
        log.error(f"Sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sync/status")
async def sync_status():
    pool = get_pool()
    runs = await pool.fetch(
        "SELECT id, job_type, started_at, completed_at, status, "
        "sessions_synced, bets_synced, results_synced, error_message "
        "FROM scheduler_runs ORDER BY started_at DESC LIMIT 20"
    )
    return {
        "scheduler": get_scheduler_status(),
        "recent_runs": [dict(r) for r in runs],
    }


@app.post("/api/sync/configure")
async def configure_sync(cfg: SchedulerConfig):
    configure_scheduler(cfg.poll_interval_minutes, cfg.enabled)
    return {"status": "ok", "config": get_scheduler_status()}


# ══════════════════════════════════════════════════════════════
# DATA ENDPOINTS (from local DB)
# ══════════════════════════════════════════════════════════════

@app.get("/api/sessions")
async def list_sessions(
    date: Optional[str] = None,
    mode: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    pool = get_pool()
    conditions = []
    params = []
    idx = 1

    if date:
        conditions.append(f"date = ${idx}")
        params.append(date)
        idx += 1
    if mode:
        conditions.append(f"mode = ${idx}")
        params.append(mode)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    rows = await pool.fetch(
        f"SELECT session_id, mode, date, start_time, stop_time, status, "
        f"total_bets, total_stake, total_liability, markets_processed, "
        f"ingested_at FROM sessions {where} "
        f"ORDER BY start_time DESC LIMIT ${idx} OFFSET ${idx+1}",
        *params,
    )
    count = await pool.fetchval(
        f"SELECT COUNT(*) FROM sessions {where}",
        *params[:-2],
    )
    return {"count": count, "sessions": [dict(r) for r in rows]}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    pool = get_pool()
    session = await pool.fetchrow(
        "SELECT * FROM sessions WHERE session_id = $1", session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    bets = await pool.fetch(
        "SELECT * FROM bets WHERE session_id = $1 ORDER BY bet_timestamp",
        session_id,
    )
    results = await pool.fetch(
        "SELECT * FROM results WHERE session_id = $1 ORDER BY race_time",
        session_id,
    )
    return {
        "session": dict(session),
        "bets": [dict(b) for b in bets],
        "results": [dict(r) for r in results],
    }


@app.get("/api/bets")
async def list_bets(
    date: Optional[str] = None,
    rule: Optional[str] = None,
    venue: Optional[str] = None,
    country: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    pool = get_pool()
    conditions = []
    params = []
    idx = 1

    if date:
        conditions.append(f"b.bet_timestamp::date = ${idx}")
        params.append(date)
        idx += 1
    if rule:
        conditions.append(f"b.rule_applied = ${idx}")
        params.append(rule)
        idx += 1
    if venue:
        conditions.append(f"b.venue ILIKE ${idx}")
        params.append(f"%{venue}%")
        idx += 1
    if country:
        conditions.append(f"b.country = ${idx}")
        params.append(country)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    rows = await pool.fetch(
        f"SELECT b.*, s.mode as session_mode, s.date as session_date "
        f"FROM bets b JOIN sessions s ON b.session_id = s.session_id "
        f"{where} ORDER BY b.bet_timestamp DESC LIMIT ${idx} OFFSET ${idx+1}",
        *params,
    )
    count = await pool.fetchval(
        f"SELECT COUNT(*) FROM bets b JOIN sessions s ON b.session_id = s.session_id {where}",
        *params[:-2],
    )
    return {"count": count, "bets": [dict(r) for r in rows]}


@app.get("/api/results")
async def list_results(
    date: Optional[str] = None,
    venue: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    pool = get_pool()
    conditions = []
    params = []
    idx = 1

    if date:
        conditions.append(f"r.race_time::date = ${idx}")
        params.append(date)
        idx += 1
    if venue:
        conditions.append(f"r.venue ILIKE ${idx}")
        params.append(f"%{venue}%")
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    rows = await pool.fetch(
        f"SELECT r.*, s.mode as session_mode, s.date as session_date "
        f"FROM results r JOIN sessions s ON r.session_id = s.session_id "
        f"{where} ORDER BY r.race_time DESC LIMIT ${idx} OFFSET ${idx+1}",
        *params,
    )
    count = await pool.fetchval(
        f"SELECT COUNT(*) FROM results r JOIN sessions s ON r.session_id = s.session_id {where}",
        *params[:-2],
    )
    return {"count": count, "results": [dict(r) for r in rows]}


@app.get("/api/summary")
async def get_summary(
    date: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    pool = get_pool()
    conditions = []
    params = []
    idx = 1

    if date:
        conditions.append(f"date = ${idx}")
        params.append(date)
        idx += 1
    if from_date:
        conditions.append(f"date >= ${idx}")
        params.append(from_date)
        idx += 1
    if to_date:
        conditions.append(f"date <= ${idx}")
        params.append(to_date)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    stats = await pool.fetchrow(
        f"SELECT COUNT(*) as total_sessions, "
        f"COALESCE(SUM(total_bets), 0) as total_bets, "
        f"COALESCE(SUM(total_stake), 0) as total_stake, "
        f"COALESCE(SUM(total_liability), 0) as total_liability, "
        f"COALESCE(SUM(markets_processed), 0) as total_markets "
        f"FROM sessions {where}",
        *params,
    )

    # Bets by rule
    rule_rows = await pool.fetch(
        f"SELECT b.rule_applied, COUNT(*) as count "
        f"FROM bets b JOIN sessions s ON b.session_id = s.session_id "
        f"{where.replace('date', 's.date')} "
        f"GROUP BY b.rule_applied ORDER BY count DESC",
        *params,
    )

    # Bets by date
    date_rows = await pool.fetch(
        f"SELECT s.date, COUNT(*) as count "
        f"FROM bets b JOIN sessions s ON b.session_id = s.session_id "
        f"{where.replace('date', 's.date')} "
        f"GROUP BY s.date ORDER BY s.date DESC LIMIT 30",
        *params,
    )

    # Mode counts
    mode_rows = await pool.fetch(
        f"SELECT b.dry_run, COUNT(*) as count "
        f"FROM bets b JOIN sessions s ON b.session_id = s.session_id "
        f"{where.replace('date', 's.date')} "
        f"GROUP BY b.dry_run",
        *params,
    )

    live_bets = 0
    dry_run_bets = 0
    for m in mode_rows:
        if m["dry_run"]:
            dry_run_bets = m["count"]
        else:
            live_bets = m["count"]

    return {
        "total_sessions": stats["total_sessions"],
        "total_bets": stats["total_bets"],
        "total_stake": float(stats["total_stake"]),
        "total_liability": float(stats["total_liability"]),
        "total_markets": stats["total_markets"],
        "live_bets": live_bets,
        "dry_run_bets": dry_run_bets,
        "bets_by_rule": {r["rule_applied"]: r["count"] for r in rule_rows},
        "bets_by_date": {str(r["date"]): r["count"] for r in date_rows},
    }


# ── Engine proxies ───────────────────────────────────────────

@app.get("/api/engine/state")
async def engine_state():
    try:
        return await lay_engine.get_state()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Lay Engine unreachable: {e}")


@app.get("/api/engine/rules")
async def engine_rules():
    try:
        return await lay_engine.get_rules()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Lay Engine unreachable: {e}")


# ══════════════════════════════════════════════════════════════
# REPORTS ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.get("/api/reports")
async def list_reports():
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, date, title, status, sessions_count, bets_count, "
        "total_stake, total_liability, created_at, updated_at "
        "FROM reports ORDER BY date DESC"
    )
    return {"reports": [dict(r) for r in rows]}


@app.post("/api/reports/generate")
async def generate_report_endpoint(req: ReportRequest, bg: BackgroundTasks):
    pool = get_pool()

    # Check we have data for the date
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM sessions WHERE date = $1", req.date
    )
    if count == 0:
        raise HTTPException(
            status_code=400,
            detail=f"No session data for {req.date}. Run a sync first.",
        )

    # Create report record
    report_id = await pool.fetchval(
        "INSERT INTO reports (date, title, status, sessions_count) "
        "VALUES ($1, $2, 'generating', $3) RETURNING id",
        req.date,
        f"Daily Report — {req.date}",
        count,
    )

    # Run generation in background
    bg.add_task(_run_report_generation, report_id, req.date)

    return {"status": "ok", "report_id": report_id, "message": "Report generation started"}


async def _run_report_generation(report_id: int, report_date: str):
    """Background task to generate report with AI analysis + PDF."""
    try:
        from report_generator import generate_report
        pool = get_pool()
        await generate_report(
            pool=pool,
            lay_engine=lay_engine,
            anthropic_client=get_anthropic(),
            report_id=report_id,
            report_date=report_date,
            rules_description=RULES_DESCRIPTION,
        )
    except Exception as e:
        log.error(f"Report generation failed for {report_date}: {e}")
        pool = get_pool()
        await pool.execute(
            "UPDATE reports SET status='failed', summary_text=$1, updated_at=NOW() WHERE id=$2",
            str(e),
            report_id,
        )


@app.get("/api/reports/{report_id}")
async def get_report(report_id: int):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, date, title, status, summary_text, analysis_json, "
        "sessions_count, bets_count, total_stake, total_liability, "
        "created_at, updated_at FROM reports WHERE id = $1",
        report_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return dict(row)


@app.get("/api/reports/{report_id}/pdf")
async def download_report_pdf(report_id: int):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT pdf_bytes, date, title FROM reports WHERE id = $1", report_id
    )
    if not row or not row["pdf_bytes"]:
        raise HTTPException(status_code=404, detail="PDF not available")
    return Response(
        content=bytes(row["pdf_bytes"]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="chimera_report_{row["date"]}.pdf"'
        },
    )


@app.delete("/api/reports/{report_id}")
async def delete_report(report_id: int):
    pool = get_pool()
    result = await pool.execute("DELETE FROM reports WHERE id = $1", report_id)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Report not found")
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════
# AI CHAT ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.post("/api/chat")
async def chat(req: ChatRequest):
    client = get_anthropic()
    if not client:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")

    pool = get_pool()

    # Build context from DB
    if req.date:
        sessions = await pool.fetch(
            "SELECT raw_json FROM sessions WHERE date = $1", req.date
        )
        reports = await pool.fetch(
            "SELECT analysis_json, summary_text FROM reports WHERE date = $1 AND status='ready'",
            req.date,
        )
    else:
        sessions = await pool.fetch(
            "SELECT raw_json FROM sessions ORDER BY date DESC LIMIT 5"
        )
        reports = await pool.fetch(
            "SELECT analysis_json, summary_text FROM reports WHERE status='ready' ORDER BY date DESC LIMIT 3"
        )

    knowledge = await pool.fetch(
        "SELECT category, content FROM knowledge_base ORDER BY created_at DESC LIMIT 50"
    )

    # Compact session data for prompt
    session_data = []
    for s in sessions:
        raw = s["raw_json"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        session_data.append({
            "session_id": raw.get("session_id"),
            "mode": raw.get("mode"),
            "date": raw.get("date"),
            "status": raw.get("status"),
            "summary": raw.get("summary"),
            "bets_count": len(raw.get("bets", [])),
            "results_count": len(raw.get("results", [])),
        })

    report_data = []
    for r in reports:
        report_data.append({
            "summary": r["summary_text"],
            "analysis": r["analysis_json"] if isinstance(r["analysis_json"], dict) else json.loads(r["analysis_json"]) if r["analysis_json"] else None,
        })

    kb_data = [{"category": k["category"], "content": k["content"]} for k in knowledge]

    system_prompt = f"""You are the CHIMERA Data Analyst — a specialist AI assistant for horse racing lay betting data analysis.
You work with data from the CHIMERA Lay Engine, which automates lay bets on Betfair Exchange.

{RULES_DESCRIPTION}

SESSION DATA FROM DATABASE:
{json.dumps(session_data, indent=2, default=str)}

PREVIOUS ANALYSIS REPORTS:
{json.dumps(report_data, indent=2, default=str)}

ACCUMULATED KNOWLEDGE BASE:
{json.dumps(kb_data, indent=2, default=str)}

Instructions:
- Provide data-driven, specific analysis. Reference actual numbers from the data.
- Be conversational but precise.
- If asked about a specific date, focus on that date's data.
- Highlight patterns, anomalies, and actionable insights.
- When discussing performance, consider rule distribution, stake/liability ratios, and venue patterns."""

    # Build messages
    messages = [{"role": m.role, "content": m.content} for m in req.history]
    messages.append({"role": "user", "content": req.message})

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        )
        reply = response.content[0].text
        return {"reply": reply}
    except Exception as e:
        log.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Audio endpoints ──────────────────────────────────────────

@app.post("/api/audio/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    client = get_openai()
    if not client:
        raise HTTPException(status_code=503, detail="OpenAI API key not configured")

    audio_bytes = await file.read()
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = file.filename or "recording.webm"

    try:
        transcript = client.audio.transcriptions.create(
            model="whisper-1", file=audio_file
        )
        return {"text": transcript.text}
    except Exception as e:
        log.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/audio/speak")
async def speak_text(request: dict):
    client = get_openai()
    if not client:
        raise HTTPException(status_code=503, detail="OpenAI API key not configured")

    text = request.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    try:
        response = client.audio.speech.create(
            model="tts-1", voice="nova", input=text[:4096]
        )
        audio_bytes = response.content
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        log.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# KNOWLEDGE BASE ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.get("/api/knowledge")
async def list_knowledge(
    category: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    pool = get_pool()
    conditions = []
    params = []
    idx = 1

    if category:
        conditions.append(f"category = ${idx}")
        params.append(category)
        idx += 1
    if date:
        conditions.append(f"date_relevant = ${idx}")
        params.append(date)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    rows = await pool.fetch(
        f"SELECT * FROM knowledge_base {where} "
        f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx+1}",
        *params,
    )
    return {"entries": [dict(r) for r in rows]}


@app.post("/api/knowledge")
async def add_knowledge(entry: KnowledgeEntry):
    pool = get_pool()
    row_id = await pool.fetchval(
        "INSERT INTO knowledge_base (category, content, source_type, date_relevant) "
        "VALUES ($1, $2, 'manual', $3) RETURNING id",
        entry.category,
        entry.content,
        entry.date_relevant,
    )
    return {"status": "ok", "id": row_id}


@app.delete("/api/knowledge/{entry_id}")
async def delete_knowledge(entry_id: int):
    pool = get_pool()
    result = await pool.execute(
        "DELETE FROM knowledge_base WHERE id = $1", entry_id
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "ok"}
