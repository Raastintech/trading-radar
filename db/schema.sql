-- gem-trader SQLite schema
-- Run once on the Ubuntu server to initialise the DB:
--   sqlite3 /home/gem/trading-production/db/trading.db < schema.sql
--
-- The DecisionLogger also creates these tables via CREATE TABLE IF NOT EXISTS,
-- so this file is for reference and manual migration only.

-- ── Trade decisions (primary audit log) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,
    run_id          TEXT,
    ts              TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    strategy        TEXT NOT NULL,       -- SNIPER | VOYAGER | REMORA | SHORT | CONTRARIAN
    direction       TEXT NOT NULL,       -- LONG | SHORT
    signal_score    REAL,
    shares          REAL,
    entry_price     REAL,
    stop_loss       REAL,
    target_price    REAL,
    risk_reward     REAL,
    order_id        TEXT,
    position_opened INTEGER DEFAULT 0,
    position_closed INTEGER DEFAULT 0,
    exit_price      REAL,
    pnl             REAL,
    pnl_pct         REAL,
    veto_votes      TEXT,               -- JSON blob of all agent votes
    notes           TEXT
);

-- ── Veto council log ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS veto_log (
    id          TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    verdict     TEXT NOT NULL,           -- APPROVED | VETOED
    agent       TEXT,
    reason      TEXT,
    run_id      TEXT
);

-- ── Macro events (refreshed from FMP every scan cycle) ───────────────────────
CREATE TABLE IF NOT EXISTS macro_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time_utc  TEXT,
    event_name      TEXT,
    impact          TEXT,                -- High | Medium | Low
    actual          TEXT,
    forecast        TEXT,
    previous        TEXT,
    source          TEXT DEFAULT 'fmp'
);

-- ── Voyager paper-validation signal log ─────────────────────────────────────
-- One row per Voyager signal that passes all gates during the paper cycle.
-- Outcome columns (outcome_30d etc.) are NULL at signal time and filled in by
-- research/paper_trades/voyager_paper_report.py --update-outcomes.
--
-- Doctrine: VOYAGER = LONG only. direction is always 'LONG'.
-- Paper validation target: 30 signals, ≥30-day hold before assessing outcome.
CREATE TABLE IF NOT EXISTS voyager_paper_signals (
    id                       TEXT PRIMARY KEY,
    logged_at                TEXT NOT NULL,       -- ISO UTC timestamp
    ticker                   TEXT NOT NULL,
    direction                TEXT NOT NULL DEFAULT 'LONG',
    archetype                TEXT NOT NULL,       -- BASE_ACCUMULATION | TREND_PULLBACK | EARLY_ACCUMULATION
    base_score               INTEGER NOT NULL,
    thirteen_f_pts           INTEGER NOT NULL DEFAULT 0,
    final_score              INTEGER NOT NULL,
    thirteen_f_flow          TEXT,               -- BUYING | SELLING | MIXED | NEUTRAL | UNKNOWN
    thirteen_f_confidence    TEXT,               -- HIGH | MODERATE | LOW | UNKNOWN
    thirteen_f_buying        INTEGER,
    thirteen_f_selling       INTEGER,
    thirteen_f_quarter       TEXT,
    size_bucket              TEXT,               -- large | mid | emerging | unknown
    market_cap               REAL,               -- raw market cap at signal time
    entry_price              REAL NOT NULL,
    stop_loss                REAL NOT NULL,
    target_price             REAL NOT NULL,
    risk_reward              REAL NOT NULL,
    ma50                     REAL,
    ma200                    REAL,
    rs_50d                   REAL,               -- % vs SPY 50d
    rs_130d                  REAL,               -- % vs SPY 130d
    dvol_ratio               REAL,
    up_vol_ratio             REAL,
    extension_ma50           REAL,               -- % distance above/below MA50
    fund_score               INTEGER,
    fund_note                TEXT,
    vix_at_entry             REAL,
    spy_above_ma50           INTEGER,            -- 1 / 0 / NULL
    spy_above_ma200          INTEGER,            -- 1 / 0 / NULL
    -- Outcome columns — NULL until measured
    outcome_30d              REAL,
    outcome_90d              REAL,
    outcome_180d             REAL,
    outcome_30d_date         TEXT,               -- date measurement was taken
    outcome_90d_date         TEXT,
    outcome_180d_date        TEXT,
    above_ma200_at_30d       INTEGER,            -- 1=held structural stop, 0=broke, NULL=not yet
    -- Exit tracking (if stopped out before horizon)
    signal_status            TEXT DEFAULT 'open', -- open | stopped_out | closed_manual
    exit_price               REAL,
    exit_date                TEXT,
    exit_reason              TEXT,
    notes                    TEXT
);

-- ── Useful indexes ────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_decisions_ticker   ON decisions(ticker);
CREATE INDEX IF NOT EXISTS idx_decisions_strategy ON decisions(strategy);
CREATE INDEX IF NOT EXISTS idx_decisions_open     ON decisions(position_opened, position_closed);
CREATE INDEX IF NOT EXISTS idx_veto_ticker        ON veto_log(ticker);
CREATE INDEX IF NOT EXISTS idx_macro_time         ON macro_events(event_time_utc);
CREATE INDEX IF NOT EXISTS idx_vps_ticker         ON voyager_paper_signals(ticker);
CREATE INDEX IF NOT EXISTS idx_vps_archetype      ON voyager_paper_signals(archetype);
CREATE INDEX IF NOT EXISTS idx_vps_status         ON voyager_paper_signals(signal_status);
CREATE INDEX IF NOT EXISTS idx_vps_logged_at      ON voyager_paper_signals(logged_at);
