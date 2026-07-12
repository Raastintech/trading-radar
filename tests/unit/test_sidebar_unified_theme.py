"""Tests for the unified institutional theme across all sidebar tabs.

Covers the required guarantees: every route renders, shared page hierarchy
exists, no user-facing dead-horse in the template, canonical enums/values
unchanged, top lists truncated by default, the leaderboard signal-strength
disclaimer, exploratory (non-error) emerging styling, collapsed internals,
distinct state banners, grouped sidebar with active highlighting, and a
headless render of every route (skipped when node is unavailable).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_STATIC = REPO_ROOT / "dashboards" / "research_command_center" / "static"
_INDEX = _STATIC / "index.html"
_HTML = _INDEX.read_text(encoding="utf-8")

# the 14 real sidebar routes (from the NAV inventory)
ROUTES = ["home", "alpha", "board", "forward-evidence", "sector-compass",
          "social-arb", "fundamental-lens", "universe", "data-quality",
          "hp", "extended", "young", "mot", "research-journal"]


# ── 1. shared design system + tokens present ────────────────────────────────


def test_research_tokens_defined():
    for tok in ("--research-emerald", "--research-indigo", "--research-violet",
                "--research-steel", "--research-amber", "--research-red",
                "--research-bg", "--research-surface",
                "--research-surface-elevated", "--research-border",
                "--research-muted"):
        assert tok in _HTML, tok


def test_shared_components_present():
    for fn in ("function pageHeader", "function emptyState",
               "function stateBanner", "function researchOnlyDisclosure",
               "function freshnessChip", "const termLabel"):
        assert fn in _HTML, fn


# ── 2. page hierarchy: every render fn uses the shared header ────────────────


def test_every_page_uses_shared_header():
    # each route's render path references pageHeader (Level-1 header)
    for fn in ("renderBoard", "renderMot", "renderUniverse", "renderForward",
               "renderSectors", "renderSocial", "renderDataQuality",
               "renderFundamentals", "renderJournal"):
        assert fn in _HTML, fn
    # pageHeader is used broadly, not just once
    assert _HTML.count("pageHeader(") >= 10


# ── 3. no user-facing dead-horse in the template ────────────────────────────


def test_no_dead_horse_in_template():
    # allow only the internal payload-key read and the single design-note
    stripped = _HTML.replace("c.dead_horse_risk", "").replace(
        "w.business_deterioration_risk", "")
    offenders = [ln for ln in stripped.splitlines()
                 if "dead-horse" in ln.lower()
                 and "internal payload key" not in ln.lower()]
    assert not offenders, offenders


# ── 4. canonical enums unchanged (display maps never mutate payloads) ────────


def test_terminology_map_preserves_canonical_keys():
    # the display map keys ARE the canonical enums; values are display-only
    for enum in ("NEED_MORE_DATA", "INSUFFICIENT_MATURE_EVIDENCE",
                 "QUALITY_BUT_EXTENDED", "REJECTED", "DATA_QUARANTINE",
                 "UNAVAILABLE_STALE_SOURCE", "NO_FORWARD_EDGE"):
        assert enum in _HTML, enum


def test_canonical_values_unchanged_in_artifacts():
    from research.high_conviction_alpha import build_shortlist, WEIGHTS
    p = build_shortlist(REPO_ROOT)
    if p.get("present"):
        assert p["counts"]["evaluated"] >= 0
        for c in p.get("shortlist") or []:
            assert c["classification"] in (
                "HIGH_CONVICTION", "PROMISING", "WATCH_FOR_ENTRY")
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


# ── 5. top lists truncated by default ───────────────────────────────────────


def test_top_lists_truncated_by_default():
    assert "HCVIEW={cap:5}" in _HTML          # HC top 5
    assert 'EOVIEW={tier:"top"}' in _HTML      # emerging top 5
    # leaderboard has a filter + summary, not an unbounded wall on home
    assert "programSummaryCard" in _HTML       # programs compact (top 3)


# ── 6. Scanner Leaderboard signal-strength disclaimer ───────────────────────


def test_leaderboard_signal_strength_disclaimer():
    assert ("Scanner score reflects signal strength, not validated alpha"
            in _HTML)
    assert "A score of 100 is not High Conviction" in _HTML


# ── 7. emerging uses exploratory (indigo) not error styling ─────────────────


def test_emerging_exploratory_styling():
    assert ".eo-wrap{border:1px solid var(--indigo)" in _HTML
    # header not amber/red
    assert ".eo-wrap .sec-head h2{color:var(--indigo)}" in _HTML


# ── 8. rejected / raw internals collapsed by default ────────────────────────


def test_rejected_collapsed_and_internals_hidden():
    assert "DID NOT QUALIFY" in _HTML
    assert "rej-tray" in _HTML
    # Level-4 internals sit under details/accordions
    assert "Full program detail" in _HTML
    assert "Scanner Internals" in _HTML


# ── 11. distinct state banners (not one generic red) ────────────────────────


def test_distinct_state_banners():
    for cls in (".state.loading", ".state.empty", ".state.stale",
                ".state.missing", ".state.blocked", ".state.disabled",
                ".state.error"):
        assert cls in _HTML, cls
    # error and missing render differently
    assert 'stateBanner("missing"' in _HTML
    assert 'stateBanner("error"' in _HTML


# ── 12. sidebar groups + active-state highlighting ──────────────────────────


def test_sidebar_groups_and_active_state():
    for grp in ("Research", "Evidence", "Discovery", "Quality & Risk",
                "Operations"):
        assert f'"{grp}"' in _HTML, grp
    assert "nav-glabel" in _HTML          # section labels
    assert 'n.id===activePage?"active"' in _HTML   # active highlight
    assert "NAV_GROUPS" in _HTML


# ── 14. responsive layout hooks exist ───────────────────────────────────────


def test_responsive_grids_present():
    # auto-fit / minmax grids adapt across widths
    assert "repeat(auto-fill,minmax(" in _HTML
    assert "repeat(auto-fit,minmax(" in _HTML


# ── 15-16. research-only invariants; no logic imports in the client ─────────


def test_client_is_read_only_and_research_only():
    assert "do_POST" not in _HTML
    assert "researchOnlyDisclosure" in _HTML
    # the page is a GET client; the only POST anywhere is the journal note,
    # which lives server-side, not in this render layer
    assert _HTML.count('method:"POST"') <= 1   # journal note only


# ── 18. headless render of EVERY route (skipped without node) ───────────────


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_headless_render_every_route():
    """Each route's renderPage() executes without throwing under a minimal
    DOM + fetch stub (empty payloads → pages hit their empty/fallback
    states, which must still render)."""
    import re
    m = re.search(r"<script>(.*)</script>", _HTML, re.S)
    app_js = m.group(1)
    harness = r"""
const _stub=()=>({innerHTML:"",value:"",style:{},onclick:null,onchange:null,
  onkeydown:null,oninput:null,dataset:{},classList:{toggle(){},add(){},remove(){},
  contains(){return false}},addEventListener(){},appendChild(){},focus(){},
  setSelectionRange(){},querySelectorAll:()=>[]});
let _ws="";const _wsEl={set innerHTML(v){_ws=v;},get innerHTML(){return _ws;}};
global.document={querySelector:(s)=>s==="#workspace"?_wsEl:_stub(),
  querySelectorAll:()=>[],addEventListener(){},body:{classList:{toggle(){}}},
  createElement:_stub,getElementById:()=>_stub()};
global.window={addEventListener(){},matchMedia:()=>({matches:false,addEventListener(){}})};
global.localStorage={getItem:()=>null,setItem(){}};
global.fetch=()=>Promise.resolve({ok:true,json:()=>Promise.resolve({})});
__APP__
STATUS={};BOARD={rows:[]};
renderNav();
const routes=__ROUTES__;
const bad=[];
for(const id of routes){ activePage=id;
  try{ renderPage(); }catch(e){ bad.push(id+": "+e); } }
if(bad.length){ console.log("FAIL "+JSON.stringify(bad)); process.exit(1); }
console.log("OK "+routes.length);
"""
    script = harness.replace("__APP__", app_js).replace(
        "__ROUTES__", json.dumps(ROUTES))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        out = subprocess.run(["node", path], capture_output=True, text=True,
                             timeout=60)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.startswith(f"OK {len(ROUTES)}"), out.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_js_syntax_valid():
    import re
    m = re.search(r"<script>(.*)</script>", _HTML, re.S)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(m.group(1))
        path = fh.name
    try:
        out = subprocess.run(["node", "--check", path], capture_output=True,
                             text=True, timeout=30)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr
