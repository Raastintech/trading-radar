"""Research Command Center — local read-only web server.

Serves the dashboard single page + JSON API from the research artifacts.
RESEARCH_ONLY / CACHE-ONLY / CRED-FREE: stdlib http.server, no provider
calls, no execution paths, no core.config import.

The single exception to GET-only is POST /api/journal (Phase 6): manual
research notes appended to data/research/journal.jsonl — the dashboard's
only write path, disabled by default and triple-guarded:
  1. GEM_RCC_ENABLE_JOURNAL_WRITES=true must be set (default: 403).
  2. If GEM_RCC_JOURNAL_TOKEN is set, the X-Research-Journal-Token header
     must match (constant-time compare; token never echoed or logged).
  3. Without a token, only loopback clients may post — a 0.0.0.0 bind
     must not accept LAN notes unless a token was explicitly configured.
Every other write method/path still returns 405.

Run:
    .venv/bin/python -m dashboards.research_command_center.server
    # then open http://127.0.0.1:8787

Options:
    --port N     (default 8787)
    --root PATH  (artifact root override; default = repo root)
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE.parents[1]) not in sys.path:
    sys.path.insert(0, str(HERE.parents[1]))

from dashboards.research_command_center.data_adapter import (  # noqa: E402
    ArtifactStore,
    build_cohort_attribution,
    build_data_quality,
    build_emerging_outlier,
    build_intel,
    build_forward_cohorts,
    build_fundamentals,
    build_high_conviction,
    build_leaderboard,
    build_research_operating_policy,
    build_sector_compass,
    build_social_overview,
    build_status,
    build_ticker_detail,
    build_ticker_series,
    JOURNAL_LIMITS,
    JOURNAL_STATUSES,
    append_journal_entry,
    read_journal,
    validate_journal_entry,
)

JOURNAL_MAX_PAYLOAD = 64_000  # bytes; a note tops out well under this
LOOPBACK_IPS = {"127.0.0.1", "::1"}


def journal_writes_enabled() -> bool:
    return os.environ.get("GEM_RCC_ENABLE_JOURNAL_WRITES", "").strip().lower() \
        in {"1", "true", "yes"}


def journal_post_authorized(client_ip: str,
                            token_header: str | None) -> tuple:
    """(allowed, error_message).  Token beats loopback; no token restricts
    posting to loopback clients so a 0.0.0.0 bind stays LAN-safe."""
    if not journal_writes_enabled():
        return False, "journal writes disabled"
    token = os.environ.get("GEM_RCC_JOURNAL_TOKEN", "")
    if token:
        if not token_header or not hmac.compare_digest(token, token_header):
            return False, "invalid or missing X-Research-Journal-Token"
        return True, None
    if client_ip not in LOOPBACK_IPS:
        return False, ("journal token not configured — non-loopback clients "
                       "may not post; set GEM_RCC_JOURNAL_TOKEN to enable "
                       "LAN note capture")
    return True, None

INDEX_HTML = HERE / "static" / "index.html"


def _git_head_short() -> str:
    """Repo HEAD (short hash) via plain file reads — no subprocess, so a
    broken git install can never take the dashboard down."""
    try:
        git_dir = HERE.parents[1] / ".git"
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:7]
        ref = head.split(" ", 1)[1].strip()
        ref_path = git_dir / ref
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()[:7]
        packed = git_dir / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0][:7]
    except Exception:
        pass
    return "unknown"


# Captured once at process start: the page footer compares these against
# a fresh read at request time so a server running stale code is visible
# at a glance (the exact failure mode of 2026-07-10).
SERVER_STARTED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
SERVER_CODE_VERSION = _git_head_short()

# Later-phase routes are stubbed on purpose — see the phase plan.  They exist
# so the sidebar can link somewhere honest, not to hide unbuilt features.
STUB_PAGES = {}  # every planned page is implemented


class Handler(BaseHTTPRequestHandler):
    server_version = "ResearchCommandCenter/0.1"
    store: ArtifactStore = ArtifactStore()

    # ── plumbing ────────────────────────────────────────────────────────────
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # quiet by default
        pass

    # ── routes (GET only — this dashboard can never mutate anything) ───────
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path == "/":
                if INDEX_HTML.exists():
                    self._send(200, INDEX_HTML.read_bytes(),
                               "text/html; charset=utf-8")
                else:
                    self._send(500, b"index.html missing", "text/plain")
            elif path == "/api/status":
                self._json(build_status(self.store))
            elif path == "/api/meta":
                current = _git_head_short()
                self._json({
                    "server_started_at": SERVER_STARTED_AT,
                    "server_code_version": SERVER_CODE_VERSION,
                    "repo_code_version": current,
                    "stale": current != SERVER_CODE_VERSION,
                    "research_only": True,
                })
            elif path == "/api/leaderboard":
                self._json(build_leaderboard(self.store))
            elif path == "/api/high-conviction":
                self._json(build_high_conviction(self.store))
            elif path == "/api/emerging-outlier":
                self._json(build_emerging_outlier(self.store))
            elif path == "/api/intel":
                self._json(build_intel(self.store))
            elif path.startswith("/api/ticker/"):
                ticker = path.rsplit("/", 1)[-1]
                self._json(build_ticker_detail(ticker, self.store))
            elif path.startswith("/api/series/"):
                ticker = path.rsplit("/", 1)[-1]
                self._json(build_ticker_series(ticker, self.store))
            elif path == "/api/forward-cohorts":
                self._json(build_forward_cohorts(self.store))
            elif path == "/api/cohort-attribution":
                self._json(build_cohort_attribution(self.store))
            elif path == "/api/research-operating-policy":
                self._json(build_research_operating_policy(self.store))
            elif path == "/api/sectors":
                self._json(build_sector_compass(self.store))
            elif path == "/api/social":
                self._json(build_social_overview(self.store))
            elif path == "/api/data-quality":
                self._json(build_data_quality(self.store))
            elif path.startswith("/api/fundamentals/"):
                ticker = path.rsplit("/", 1)[-1]
                self._json(build_fundamentals(ticker, self.store))
            elif path == "/api/journal":
                q = parse_qs(urlparse(self.path).query)
                payload = read_journal(
                    self.store,
                    ticker=(q.get("ticker") or [None])[0],
                    status=(q.get("status") or [None])[0],
                    tag=(q.get("tag") or [None])[0],
                    limit=(q.get("limit") or [100])[0],
                )
                payload["enabled"] = True
                payload["write_enabled"] = journal_writes_enabled()
                self._json(payload)
            elif path == "/api/journal/schema":
                self._json({
                    "statuses": JOURNAL_STATUSES,
                    "limits": JOURNAL_LIMITS,
                    "write_enabled": journal_writes_enabled(),
                    "safety": [
                        "Append-only; writes touch only "
                        "data/research/journal.jsonl.",
                        "Writes disabled unless "
                        "GEM_RCC_ENABLE_JOURNAL_WRITES=true.",
                        "Set GEM_RCC_JOURNAL_TOKEN to allow non-loopback "
                        "posting; otherwise loopback only.",
                        "Manual research notes only — never a signal.",
                    ],
                })
            elif path.startswith("/api/stub/"):
                page = path.rsplit("/", 1)[-1]
                note = STUB_PAGES.get(page, "Not yet built")
                self._json({"status": "TODO", "page": page, "note": note,
                            "research_only": True})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # never crash the server on a bad artifact
            self._json({"error": f"internal: {exc}"}, 500)

    def do_POST(self):  # noqa: N802 — journal notes are the ONLY write path
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/api/journal":
            self._json({"error": "read-only dashboard"}, 405)
            return
        try:
            client_ip = self.client_address[0]
            token_header = self.headers.get("X-Research-Journal-Token")
            allowed, err = journal_post_authorized(client_ip, token_header)
            if not allowed:
                self._json({"error": err}, 403)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._json({"error": "empty body"}, 400)
                return
            if length > JOURNAL_MAX_PAYLOAD:
                self._json({"error": "payload too large"}, 413)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._json({"error": "malformed JSON"}, 400)
                return
            entry, verr = validate_journal_entry(payload)
            if entry is None:
                self._json({"error": verr}, 400)
                return
            append_journal_entry(entry, self.store)
            self._json(entry, 201)
        except Exception as exc:
            self._json({"error": f"internal: {exc}"}, 500)

    def _reject_write(self):  # noqa: N802
        self._json({"error": "read-only dashboard"}, 405)

    do_PUT = do_DELETE = do_PATCH = _reject_write


def make_server(port: int = 8787, root: Path | None = None,
                host: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = Handler
    if root is not None:
        handler.store = ArtifactStore(root=Path(root))
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Research Command Center (read-only)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--root", type=str, default=None)
    ap.add_argument("--host", type=str, default="127.0.0.1",
                    help="Bind address. Default 127.0.0.1 (localhost only). "
                         "Use your LAN IP or 0.0.0.0 to reach the dashboard "
                         "from other machines — it is read-only but has no "
                         "auth, so only do this on a trusted network.")
    args = ap.parse_args(argv)
    srv = make_server(port=args.port, root=Path(args.root) if args.root else None,
                      host=args.host)
    print(f"Research Command Center (RESEARCH_ONLY, read-only) → "
          f"http://{args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
