import os
from secure_env import load_runtime_env

# Load environment variables only from shell or explicit external env path.
load_runtime_env("config")


class TradingConfig:
    """Configuration settings for the Sniper Trading AI"""

    # API Keys (loaded from shell env or explicit external env file)
    ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
    UNUSUAL_WHALES_KEY = os.getenv("UNUSUAL_WHALES_KEY", "")
    POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

    # Sentiment API keys
    # Keep these empty by default; provide via environment variables.
    NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
    # Support both env var names for compatibility.
    ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY", os.getenv("ALPHA_VANTAGE_KEY", ""))
    REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", None)
    REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", None)

    # Trading settings
    PAPER_TRADING = True
    ALLOW_MARKET_FALLBACK = False
    ALLOW_NAKED_ENTRIES = False
    ALLOW_SHORTS = True

    # Risk management
    MAX_POSITION_SIZE = 0.02
    MAX_DAILY_LOSS = 0.05
    WHALE_THRESHOLD = 1000000
    # Alpaca does not provide VIX index bars via stock endpoint. Use tradable volatility proxy.
    VOL_PROXY_TICKER = "VXX"

    WATCHLIST = {
        # ─────────────────────────────────────────
        # 🚀 AI / Cloud / Software Leadership
        # ─────────────────────────────────────────
        "AI_TECH": [
            "NVDA", "AMD", "MSFT", "GOOG", "AAPL", "META", "AMZN", "BABA", "ORCL", "BITU",
            "PLTR", "NET", "CRM", "NOW", "CRDO", "NVTS", "SYM", "ZETA"
        ],

        # ─────────────────────────────────────────
        # 🧠 High-Beta / Momentum / Trading Vehicles
        # ─────────────────────────────────────────
        "MOMENTUM": [
            "TQQQ", "TSLA", "SERV", "BMNR", "NBIS", "BULL", "VG", "SOXL", "RIVN", "MU"
        ],

        # ─────────────────────────────────────────
        # 🏦 Fintech / Payments / Consumer Credit
        # ─────────────────────────────────────────
        "FINTECH": [
            "HOOD", "SOFI", "PYPL", "RKT", "SEZL", "SHOP", "V"
        ],

        # ─────────────────────────────────────────
        # 🪙 Crypto / Blockchain / Bitcoin Proxies
        # ─────────────────────────────────────────
        "CRYPTO": [
            "IREN", "APLD", "COIN", "IBIT", "CIFR", "HUT"
        ],

        # ─────────────────────────────────────────
        # ☢️ Energy / Nuclear / Infrastructure
        # ─────────────────────────────────────────
        "ENERGY_INDUSTRIALS": [
            "OKLO", "EOSE", "TAC", "PWR", "CRCL"
        ],

        # ─────────────────────────────────────────
        # 🛰️ Space / Defense / Deep-Tech
        # ─────────────────────────────────────────
        "SPACE_DEFENSE": [
            "RKLB", "POET", "ASTS", "SATL", "HIMS", "ONDS", "SPCE"
        ],

        # ─────────────────────────────────────────
        # 🏥 Healthcare / Insurance / Biotech-Adj
        # ─────────────────────────────────────────
        "HEALTHCARE": [
            "HIMS", "OSCR", "UNH"
        ],

        # ─────────────────────────────────────────
        # 🛍️ Consumer / Retail / Travel
        # ─────────────────────────────────────────
        "CONSUMER": [
            "NKE", "HD", "SFM", "BROS", "CAVA", "RCL", "UAL", "NFLX", "GRAB"
        ],

    

        # ─────────────────────────────────────────
        # 📊 Market Regime / Breadth Anchors
        # (DO NOT REMOVE - critical for alignment)
        # ─────────────────────────────────────────
        "REGIME_ETFS": [
            "XLF",  # Credit / risk appetite
            "XLI",  # Broad participation
            "XLK",  # Tech leadership
            "IWM",  # Small-cap breadth
            "SPY",  # Large-cap stability
            "VXX",  # Volatility / fear gauge
            "IGV",  # Software / cloud proxy
            "XLU",  # Utilities / risk-off indicator
            "XLE",  # Energy / inflation proxy
            "XLP",  # Staples / defensive proxy
            "XLRE", # Real Estate / interest rate sensitivity
            "XLC",  # Communication Services / media proxy
            "XLY"   #   Consumer Discretionary / retail proxy
        ],
    }

    @classmethod
    def get_watchlist(cls):
        """Flatten categorized watchlist into an ordered, deduplicated ticker list."""
        raw = cls.WATCHLIST
        if isinstance(raw, dict):
            seen = set()
            tickers = []
            for group in raw.values():
                for ticker in group:
                    if ticker not in seen:
                        seen.add(ticker)
                        tickers.append(ticker)
            return tickers
        return list(raw)

    def validate(self):
        """Check if required API keys are set."""
        if not self.ALPACA_API_KEY:
            return False, "Missing ALPACA_API_KEY"
        if not self.ALPACA_SECRET_KEY:
            return False, "Missing ALPACA_SECRET_KEY"
        return True, "Configuration valid"
