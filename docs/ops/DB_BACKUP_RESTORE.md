# DB Backup & Restore

Phase 0 hardening, 2026-05.  Applies to `db/trading.db` (canonical) and
`db/trading_performance.db` (legacy duplicate, still touched by some
research scripts).

---

## What's now in place

| Layer | What it does | Where |
|---|---|---|
| **`core/db.connect()`** | Every primary writer (data_gatekeeper, decision_logger, voyager_paper_logger, paper_validation) opens through this helper. Sets `journal_mode=WAL`, `synchronous=FULL`, `busy_timeout=5000`, `foreign_keys=ON` on every connect. | `core/db.py` |
| **`scripts/db_bootstrap_hardening.py`** | One-shot bootstrap. Runs `PRAGMA integrity_check`, then flips `journal_mode=WAL` persistently in the DB header. Idempotent. Aborts on corruption. | run once after pulling Phase 0 |
| **`scripts/backup_db.sh`** | Daily backup using SQLite's online `.backup` API (WAL-safe, no torn writes). Verifies each backup with `integrity_check`. Prunes to `RETENTION=14` newest. | run from cron / systemd timer |

WAL mode means the canonical DB file plus `*.db-wal` and `*.db-shm`
sidecar files form a single logical database.  Naive `cp trading.db …`
during a write window can produce a torn copy.  **Always** use the
online backup API (`scripts/backup_db.sh` does this for you).

---

## First-time setup (run once after Phase 0 lands)

```bash
# 1. Stop the trader so no writer holds the DB.
sudo systemctl stop gem-trader

# 2. Take a manual safety backup before flipping the journal mode.
.venv/bin/python -c "import sqlite3; \
  sqlite3.connect('db/trading.db').backup(sqlite3.connect('db/trading.db.pre-wal.bak'))"

# 3. Run the bootstrap.  Aborts on integrity failure before any rewrite.
.venv/bin/python scripts/db_bootstrap_hardening.py

# 4. Confirm WAL is set.
sqlite3 db/trading.db "PRAGMA journal_mode;"   # → wal
sqlite3 db/trading.db "PRAGMA synchronous;"    # connection-local, app sets FULL on connect

# 5. Restart.
sudo systemctl start gem-trader
```

If step 3 reports any `integrity_check` failure, **do not proceed**.
Restore from the most recent good backup (see *Restore* below) and
investigate before flipping WAL.

---

## Daily backup (recommended)

Add to root's crontab (or as a systemd timer if you prefer):

```cron
# 03:00 UTC daily — runs before the 03:30 ET nightly refresh and well
# after the 19:00 ET research cycle, so the DB is quiescent.
0 3 * * *  /home/gem/trading-production/scripts/backup_db.sh \
            >> /home/gem/trading-production/logs/backup_db.log 2>&1
```

Backups land in `backups/db/` as
`trading-YYYY-MM-DDTHH-MM-SSZ.db`.  Default retention is 14 newest per
source; override with `RETENTION=N`.  Override target dir with
`BACKUP_DIR=/path`.

Each backup is verified with `integrity_check` before old ones are
pruned, so a corrupt fresh backup will not displace a known-good prior.

---

## Manual backup

```bash
# WAL-safe ad-hoc backup; runs fine while trader is up.
.venv/bin/python scripts/db_bootstrap_hardening.py    # only if first time
scripts/backup_db.sh                                  # then this
```

Or one-off via `sqlite3` directly:

```bash
sqlite3 -bail db/trading.db ".backup 'backups/db/trading-manual.db'"
sqlite3 -bail backups/db/trading-manual.db "PRAGMA integrity_check;"   # → ok
```

---

## Restore

> **Stop the trader first.**  Restoring under a live writer will corrupt
> the new file.

```bash
# 1. Stop everything that writes the DB.
sudo systemctl stop gem-trader
sudo systemctl stop gem-trader-research.timer
sudo systemctl stop gem-trader-paper-evidence.timer
sudo systemctl stop gem-trader-nightly.timer

# 2. Move the current (suspect) DB and its WAL/SHM aside — do not delete.
mv db/trading.db          db/trading.db.broken-$(date -u +%Y%m%dT%H%M%SZ)
mv db/trading.db-wal      db/trading.db-wal.broken-$(date -u +%Y%m%dT%H%M%SZ) 2>/dev/null || true
mv db/trading.db-shm      db/trading.db-shm.broken-$(date -u +%Y%m%dT%H%M%SZ) 2>/dev/null || true

# 3. Pick the newest verified backup and copy into place.
ls -1t backups/db/trading-*.db | head
cp backups/db/trading-2026-05-07T03-00-00Z.db db/trading.db    # adjust filename

# 4. Verify integrity and confirm WAL on the fresh file.
sqlite3 db/trading.db "PRAGMA integrity_check;"   # → ok
sqlite3 db/trading.db "PRAGMA journal_mode=WAL;"  # → wal
ls -lh db/trading.db

# 5. Restart.
sudo systemctl start gem-trader
```

After restart, watch `logs/gem-trader.log` for the first scan cycle to
confirm the trader can read positions and decisions.  The data_gatekeeper
will issue its DDL via `CREATE TABLE IF NOT EXISTS …` on connect — no
schema migration is required.

---

## Health check

Quick standalone check, safe to run while the trader is up:

```bash
sqlite3 db/trading.db "PRAGMA integrity_check;"        # expect: ok
sqlite3 db/trading.db "PRAGMA journal_mode;"           # expect: wal
.venv/bin/python -c "from core.db import verify_hardening; \
  print(verify_hardening('db/trading.db'))"
```

Expected output for `verify_hardening`:

```python
{'journal_mode': 'wal', 'synchronous': 2, 'busy_timeout': 0, 'foreign_keys': 0}
```

Note: `verify_hardening` uses a fresh raw connection (no app helpers), so
`synchronous`, `busy_timeout`, `foreign_keys` reflect *defaults* of that
fresh connection — not the values the app sets on its own connections.
The point of the check is `journal_mode == 'wal'`, which is the
persistent property.

---

## What this does NOT cover (deferred)

- **Off-host backups.** The current backup is on the same disk.  Real
  DR requires off-host copies (S3, rsync to a backup box, or similar).
  Track as a follow-up — for paper-only this is acceptable.
- **Point-in-time recovery.** SQLite WAL gives crash-consistency, not
  point-in-time.  Granularity is "last good daily backup."
- **Trading-state replay.** Decisions / paper trades are append-only to
  the DB and JSONL ledgers; reconstructing the in-process circuit
  breaker / portfolio state from logs is not automated.
