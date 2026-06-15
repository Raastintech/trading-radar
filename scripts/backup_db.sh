#!/usr/bin/env bash
# ============================================================
# scripts/backup_db.sh — daily SQLite backup using the online .backup API.
#
# Why .backup and not file copy:
#   - WAL-safe: the SQLite online backup API takes an internal lock and
#     copies pages in-flight, so concurrent writers are not corrupted.
#     A naive `cp trading.db backup.db` while a writer holds the WAL can
#     produce a torn backup.
#   - Atomic: the destination is opened, written, and closed transparently.
#
# Backup layout:
#     /home/gem/trading-production/backups/db/
#         trading-2026-05-07T18-30-00Z.db
#         trading_performance-2026-05-07T18-30-00Z.db
#         ...
#
# Retention:
#   Keeps the last $RETENTION (default 14) backups per source DB.  Older
#   files are deleted after a successful current backup completes.
#
# Run via cron (suggested 03:00 ET) or manually.  Exits non-zero on any
# failure so a wrapping cron MAILTO surfaces it.
#
# Restore: see docs/ops/DB_BACKUP_RESTORE.md.
# ============================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_DIR="$ROOT/db"
OUT_DIR="${BACKUP_DIR:-$ROOT/backups/db}"
RETENTION="${RETENTION:-14}"

# Sources to back up.  Match the live writers.
SOURCES=(
  "trading.db"
  "trading_performance.db"
)

mkdir -p "$OUT_DIR"

ts="$(date -u +"%Y-%m-%dT%H-%M-%SZ")"
overall_rc=0

for src in "${SOURCES[@]}"; do
  src_path="$DB_DIR/$src"
  if [[ ! -f "$src_path" ]]; then
    echo "[backup_db] skip — $src_path does not exist"
    continue
  fi

  base="${src%.db}"
  dst="$OUT_DIR/${base}-${ts}.db"
  echo "[backup_db] $src_path → $dst"

  # Use the online backup API.  -bail aborts on first error.
  if ! sqlite3 -bail "$src_path" ".backup '$dst'"; then
    echo "[backup_db] FAILED for $src" >&2
    overall_rc=1
    continue
  fi

  # Verify the backup is readable and consistent.
  if ! sqlite3 -bail "$dst" "PRAGMA integrity_check;" \
        | grep -qx "ok"; then
    echo "[backup_db] integrity check FAILED for $dst" >&2
    overall_rc=1
    continue
  fi

  # Prune old backups (keep newest $RETENTION for this source).
  # ls -1t lists newest first; tail -n +N drops the keep-list.
  pruned=0
  while IFS= read -r victim; do
    rm -f "$victim" && pruned=$((pruned + 1))
  done < <(ls -1t "$OUT_DIR/${base}"-*.db 2>/dev/null | tail -n +$((RETENTION + 1)))
  if (( pruned > 0 )); then
    echo "[backup_db] pruned $pruned old backup(s) for $base"
  fi
done

echo "[backup_db] complete  ts=$ts  rc=$overall_rc"
exit "$overall_rc"
