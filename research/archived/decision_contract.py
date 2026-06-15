from typing import Any, Dict, List


ALLOWED_DECISIONS = {
    "EXECUTE",
    "REJECT",
    "SYSTEM_REJECT",
    "CAUTION",
    "DATA_ERROR",
    "LOGIC_ERROR",
    "FILTERED_NO_SIGNAL",
    "FILTERED_LOW_RR",
    "SCANNER_REJECT",
    "MACRO_EVENT_DENY",
    # Execution lifecycle decisions used by unified trader paths
    "ENTRY_SUBMITTED",
    "PAPER_ENTRY",
    "APPROVED_FILL",
    "EXIT_SUBMITTED",
    "EXECUTION_DENIED",
    "PORTFOLIO_DENIED",
    "RISK_OVERLAY_DENIED",
    "SECURITY_DENIED",
    "NEAR_MISS",
}
ALLOWED_VOTES = {"APPROVE", "VETO", "CAUTION", "ABSTAIN"}


def _to_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _list_of_str(x: Any) -> List[str]:
    if not isinstance(x, list):
        return []
    out = []
    for v in x:
        try:
            out.append(str(v))
        except Exception:
            continue
    return out


def normalize_signal(signal: Any) -> Dict[str, Any]:
    if not isinstance(signal, dict):
        signal = {}
    return {
        "ticker": signal.get("ticker"),
        "signal": signal.get("signal"),
        "composite_score": _to_float(signal.get("composite_score"), 0.0),
        "entry_price": signal.get("entry_price"),
        "stop_loss": signal.get("stop_loss"),
        "target_price": signal.get("target_price"),
        "risk_reward": signal.get("risk_reward"),
        "risk_reward_ratio": signal.get("risk_reward_ratio"),
        "risk_reward_r": signal.get("risk_reward_r"),
        "rr": signal.get("rr"),
        # routing / position fields — preserved for scanner rejects and execution records
        "strategy": signal.get("strategy"),
        "direction": signal.get("direction"),
        "shares": signal.get("shares"),
    }


def normalize_vote(vote: Any) -> Dict[str, Any]:
    if not isinstance(vote, dict):
        return {
            "vote": "ABSTAIN",
            "reason": "bad vote payload",
            "score": None,
            "agent_error": True,
            "agent_error_type": "CONTRACT_ERROR",
            "agent_error_msg": "vote payload is not a dict",
            "details": None,
        }

    agent_error = bool(vote.get("agent_error", False))
    agent_error_type = vote.get("agent_error_type")
    agent_error_msg = vote.get("agent_error_msg")

    v = str(vote.get("vote") or "ABSTAIN").upper().strip()
    if v not in ALLOWED_VOTES:
        agent_error = True
        agent_error_type = agent_error_type or "CONTRACT_ERROR"
        agent_error_msg = agent_error_msg or f"invalid vote enum: {v}"
        v = "ABSTAIN"

    score = vote.get("score", None)
    if score is not None:
        try:
            score = float(score)
        except Exception:
            agent_error = True
            agent_error_type = agent_error_type or "CONTRACT_ERROR"
            agent_error_msg = agent_error_msg or "score must be numeric or None"
            score = None

    details = vote.get("details")
    if details is not None and not isinstance(details, dict):
        agent_error = True
        agent_error_type = agent_error_type or "CONTRACT_ERROR"
        agent_error_msg = agent_error_msg or "details must be dict or None"
        details = None

    return {
        "vote": v,
        "reason": str(vote.get("reason") or "no reason"),
        "score": score,
        "agent_error": agent_error,
        "agent_error_type": agent_error_type,
        "agent_error_msg": agent_error_msg,
        "details": details,
    }


def normalize_council_decision(decision: Any, raw_votes: Any = None) -> Dict[str, Any]:
    if not isinstance(decision, dict):
        decision = {}

    d = str(decision.get("decision") or "DATA_ERROR").upper().strip()
    if d not in ALLOWED_DECISIONS:
        d = "DATA_ERROR"

    rv = raw_votes if raw_votes is not None else decision.get("raw_votes", {})
    if not isinstance(rv, dict):
        rv = {}
    rv_norm = {str(k): normalize_vote(v) for k, v in rv.items()}

    return {
        "decision": d,
        "reason": str(decision.get("reason") or "missing reason"),
        "approve_count": _to_int(decision.get("approve_count"), 0),
        "caution_count": _to_int(decision.get("caution_count"), 0),
        "veto_count": _to_int(decision.get("veto_count"), 0),
        "avg_score": _to_float(decision.get("avg_score"), 0.0),
        "veto_reasons": _list_of_str(decision.get("veto_reasons")),
        "raw_votes": rv_norm,
        "errors": _list_of_str(decision.get("errors")),
        "confidence": decision.get("confidence"),
        "position_size_multiplier": decision.get("position_size_multiplier"),
        "total_agents": decision.get("total_agents"),
    }
