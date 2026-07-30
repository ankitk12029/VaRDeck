"""
Shared configuration for the ETL pipeline and risk analysis engine.
Single source of truth for portfolio composition and run parameters.

To change the portfolio, edit TICKERS below — everything else in the
codebase reads from here.
"""

import os

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
DB_URL = os.environ.get("DATABASE_URL", "sqlite:///portfolio_dashboard.db")
# DATABASE_URL is set locally via `export` or in CI via a GitHub Actions
# secret (see .env.example). Falls back to local SQLite if unset, so the
# project still works for anyone cloning it without a Neon account.

# SQLAlchemy's default Postgres dialect is psycopg2 (already in
# requirements.txt), and it accepts plain "postgresql://" URLs directly —
# no "+psycopg2" suffix needed. Some providers (Heroku, older Neon docs)
# still hand out the deprecated "postgres://" scheme, which SQLAlchemy 1.4+
# rejects outright, so normalize it here. Query params like
# "?sslmode=require" pass through untouched either way.
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

HELD_TICKERS = [t for t, info in TICKERS.items() if info["weight"] > 0]
WEIGHTS = np.array([TICKERS[t]["weight"] for t in HELD_TICKERS])
