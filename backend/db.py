"""
Database connection pool and schema initialization.
Uses asyncpg for async PostgreSQL with FastAPI.
"""

import os
import json
import asyncpg
import logging

log = logging.getLogger("db")

_pool: asyncpg.Pool | None = None

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://chimera:chimera@localhost:5432/chimera_sessions",
)

SCHEMA_SQL = """
-- sessions: ingested from Lay Engine API
CREATE TABLE IF NOT EXISTS sessions (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT UNIQUE NOT NULL,
    mode            TEXT NOT NULL,
    date            DATE NOT NULL,
    start_time      TIMESTAMPTZ NOT NULL,
    stop_time       TIMESTAMPTZ,
    status          TEXT NOT NULL,
    total_bets      INTEGER DEFAULT 0,
    total_stake     NUMERIC(10,2) DEFAULT 0,
    total_liability NUMERIC(10,2) DEFAULT 0,
    markets_processed INTEGER DEFAULT 0,
    raw_json        JSONB,
    ingested_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
CREATE INDEX IF NOT EXISTS idx_sessions_mode ON sessions(mode);

-- bets: individual bets extracted from sessions
CREATE TABLE IF NOT EXISTS bets (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    market_id       TEXT NOT NULL,
    selection_id    BIGINT,
    runner_name     TEXT,
    price           NUMERIC(8,2),
    size            NUMERIC(8,2),
    liability       NUMERIC(8,2),
    rule_applied    TEXT,
    venue           TEXT,
    country         TEXT,
    bet_timestamp   TIMESTAMPTZ,
    dry_run         BOOLEAN DEFAULT TRUE,
    betfair_status  TEXT,
    betfair_bet_id  TEXT,
    raw_json        JSONB,
    ingested_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bets_session ON bets(session_id);
CREATE INDEX IF NOT EXISTS idx_bets_date ON bets(bet_timestamp);
CREATE INDEX IF NOT EXISTS idx_bets_rule ON bets(rule_applied);

-- results: rule evaluation results per market
CREATE TABLE IF NOT EXISTS results (
    id                   SERIAL PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES sessions(session_id),
    market_id            TEXT NOT NULL,
    market_name          TEXT,
    venue                TEXT,
    race_time            TIMESTAMPTZ,
    favourite_name       TEXT,
    favourite_odds       NUMERIC(8,2),
    favourite_selection  BIGINT,
    second_fav_name      TEXT,
    second_fav_odds      NUMERIC(8,2),
    second_fav_selection BIGINT,
    skipped              BOOLEAN DEFAULT FALSE,
    skip_reason          TEXT,
    rule_applied         TEXT,
    evaluated_at         TIMESTAMPTZ,
    total_stake          NUMERIC(8,2),
    total_liability      NUMERIC(8,2),
    instruction_count    INTEGER DEFAULT 0,
    raw_json             JSONB,
    ingested_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_results_session ON results(session_id);
CREATE INDEX IF NOT EXISTS idx_results_venue ON results(venue);

-- reports: AI-generated analysis reports
CREATE TABLE IF NOT EXISTS reports (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    title           TEXT NOT NULL,
    status          TEXT DEFAULT 'generating',
    summary_text    TEXT,
    analysis_json   JSONB,
    pdf_bytes       BYTEA,
    sessions_count  INTEGER DEFAULT 0,
    bets_count      INTEGER DEFAULT 0,
    total_stake     NUMERIC(10,2) DEFAULT 0,
    total_liability NUMERIC(10,2) DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date);

-- knowledge_base: accumulated insights from analysis
CREATE TABLE IF NOT EXISTS knowledge_base (
    id              SERIAL PRIMARY KEY,
    category        TEXT NOT NULL,
    content         TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_id       TEXT,
    date_relevant   DATE,
    confidence      NUMERIC(3,2) DEFAULT 0.80,
    metadata        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kb_category ON knowledge_base(category);
CREATE INDEX IF NOT EXISTS idx_kb_date ON knowledge_base(date_relevant);

-- scheduler_runs: audit trail for background jobs
CREATE TABLE IF NOT EXISTS scheduler_runs (
    id              SERIAL PRIMARY KEY,
    job_type        TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    status          TEXT DEFAULT 'running',
    sessions_synced INTEGER DEFAULT 0,
    bets_synced     INTEGER DEFAULT 0,
    results_synced  INTEGER DEFAULT 0,
    error_message   TEXT,
    metadata        JSONB
);
"""


async def init_db():
    """Create connection pool and run schema migrations."""
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    log.info("Database initialized — tables ready")


def get_pool() -> asyncpg.Pool:
    """Return the current connection pool."""
    return _pool
