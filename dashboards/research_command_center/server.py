"""Research Command Center — local read-only web server.

Serves the dashboard single page + JSON API from the research artifacts.
RESEARCH_ONLY / CACHE-ONLY / CRED-FREE: stdlib http.server, GET-only
(anything else returns 405), no provider calls, no writes, no execution
paths, no core.config import.

Run:
    .venv/bin/python -m dashboards.research_command_center.server
    # then open http://127.0.0.1:8787

Options:
    --port N     (default 8787)
    --root PATH  (artifact root override; default = repo root)
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parents[1]) not in sys.path:
    sys.path.insert(0, str(HERE.parents[1]))

from dashboards.research_command_center.data_adapter import (  # noqa: E402
    ArtifactStore,
    build_forward_cohorts,
    build_leaderboard,
    build_sector_compass,
    build_social_overview,
    build_status,
    build_ticker_detail,
    build_ticker_series,
)

INDEX_HTML = HERE / "static" / "index.html"

# Later-phase routes are stubbed on purpose — see the phase plan.  They exist
# so the sidebar can link somewhere honest, not to hide unbuilt features.
STUB_PAGES = {
    "fundamental-lens": "Phase 5 pending approval — fundamentals overlay not implemented yet.",
    "data-quality": "Phase 4 pending approval — full data-quality dashboard not implemented yet.",
    "research-journal": "Phase 6 pending approval — manual notes not implemented yet.",
}


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
            elif path == "/api/leaderboard":
                self._json(build_leaderboard(self.store))
            elif path.startswith("/api/ticker/"):
                ticker = path.rsplit("/", 1)[-1]
                self._json(build_ticker_detail(ticker, self.store))
            elif path.startswith("/api/series/"):
                ticker = path.rsplit("/", 1)[-1]
                self._json(build_ticker_series(ticker, self.store))
            elif path == "/api/forward-cohorts":
                self._json(build_forward_cohorts(self.store))
            elif path == "/api/sectors":
                self._json(build_sector_compass(self.store))
            elif path == "/api/social":
                self._json(build_social_overview(self.store))
            elif path.startswith("/api/stub/"):
                page = path.rsplit("/", 1)[-1]
                note = STUB_PAGES.get(page, "Not yet built")
                self._json({"status": "TODO", "page": page, "note": note,
                            "research_only": True})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:  # never crash the server on a bad artifact
            self._json({"error": f"internal: {exc}"}, 500)

    def do_POST(self):  # noqa: N802 — read-only dashboard
        self._json({"error": "read-only dashboard"}, 405)

    do_PUT = do_DELETE = do_PATCH = do_POST


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
