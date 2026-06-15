"""
Strategy Display Name Mapping
================================
Maps internal strategy keys to user-facing display names.
"""

STRATEGY_DISPLAY_NAMES = {
    "VOYAGER": "Growth Leader",
    "SNIPER": "Breakout",
    "REMORA": "Catalyst Trade",
    "SHORT": "Short Trade",
    "CONTRARIAN": "Reaper",
}

STRATEGY_DESCRIPTIONS = {
    "VOYAGER": "Targets high-growth leaders with strong fundamentals for 6-18 month positions",
    "SNIPER": "Captures short-term breakout timing inefficiencies for 3-30 day trades",
    "REMORA": "Exploits fast catalyst dislocations for 2-48 hour positions",
    "SHORT": "Shorts early deterioration before full repricing (6-12 weeks)",
    "CONTRARIAN": "Takes the other side of panic/forced selling (Reaper overlays)",
}

STRATEGY_SHORT_NAMES = {
    "VOYAGER": "GRO",
    "SNIPER": "BRK",
    "REMORA": "CAT",
    "SHORT": "SHT",
    "CONTRARIAN": "RPR",
}


def get_strategy_display_name(strategy_key: str) -> str:
    if not strategy_key:
        return ""
    return STRATEGY_DISPLAY_NAMES.get(str(strategy_key).upper(), strategy_key)


def get_strategy_description(strategy_key: str) -> str:
    if not strategy_key:
        return ""
    return STRATEGY_DESCRIPTIONS.get(str(strategy_key).upper(), "")


def get_strategy_short_name(strategy_key: str) -> str:
    if not strategy_key:
        return ""
    return STRATEGY_SHORT_NAMES.get(str(strategy_key).upper(), str(strategy_key)[:3].upper())


def format_strategy_for_display(strategy_key: str, include_description: bool = False) -> str:
    display_name = get_strategy_display_name(strategy_key)
    if include_description:
        desc = get_strategy_description(strategy_key)
        if desc:
            return f"{display_name} - {desc}"
    return display_name


def normalize_strategy_name(strategy_key: str) -> str:
    return get_strategy_display_name(strategy_key)
