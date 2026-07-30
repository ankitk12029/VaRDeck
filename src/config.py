"""
Shared configuration for the ETL pipeline and risk analysis engine.
Single source of truth for portfolio composition and run parameters.

To change the portfolio, edit TICKERS below — everything else in the
codebase reads from here.
"""

import numpy as np

TICKERS = {
    "AAPL":  {"sector": "Technology",    "weight": 0.20},
    "MSFT":  {"sector": "Technology",    "weight": 0.15},
    "JPM":   {"sector": "Financials",    "weight": 0.15},
    "JNJ":   {"sector": "Healthcare",    "weight": 0.10},
    "XOM":   {"sector": "Energy",        "weight": 0.10},
    "PG":    {"sector": "Consumer",      "weight": 0.10},
    "SPY":   {"sector": "Benchmark",     "weight": 0.00},  # benchmark, not held
    "BND":   {"sector": "Fixed Income",  "weight": 0.10},
    "GLD":   {"sector": "Commodities",   "weight": 0.10},
}

BENCHMARK = "SPY"
RISK_FREE_RATE = 0.05          # annual, update as needed
LOOKBACK_YEARS = 3
DB_URL = "sqlite:///portfolio_dashboard.db"   # swap for PostgreSQL in prod
# For PostgreSQL: "postgresql://user:pass@localhost:5432/portfolio_db"

HELD_TICKERS = [t for t, info in TICKERS.items() if info["weight"] > 0]
WEIGHTS = np.array([TICKERS[t]["weight"] for t in HELD_TICKERS])
