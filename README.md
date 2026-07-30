# VaRDeck

A live Investment Portfolio Risk Dashboard. A Python ETL pipeline pulls real
market data from Yahoo Finance, computes a full suite of portfolio risk
metrics (VaR, stress tests, Monte Carlo, efficient frontier, and more), and
writes everything to a database that Power BI and Tableau can connect to for
an auto-refreshing dashboard.

## Architecture

```mermaid
flowchart TD
    A[Yahoo Finance API<br/>via yfinance] --> B[etl_pipeline.py<br/>prices, returns, drawdowns]
    B --> C[risk_analysis.py<br/>8 risk modules]
    B --> D[(portfolio_dashboard.db<br/>SQLite / PostgreSQL)]
    C --> D
    D --> E[Power BI<br/>DirectQuery / Import]
    D --> F[Tableau<br/>Live / Extract]
    G[GitHub Actions<br/>daily_refresh.yml] -.schedules.-> B
```

**Three layers:**

1. **Ingestion** (`src/etl_pipeline.py`) — pulls 3 years of daily adjusted
   close prices for a 9-ticker portfolio + benchmark, validates data quality,
   computes returns/risk metrics/drawdowns/correlations, writes 6 tables.
2. **Risk Engine** (`src/risk_analysis.py`) — 8 modules covering VaR (3
   methods), historical stress testing, hypothetical scenarios, risk
   decomposition, regime detection, tail risk, efficient frontier, and Monte
   Carlo simulation. Writes 16 more tables. Runs automatically at the end of
   `run_pipeline()`.
3. **Visualization** — Power BI and Tableau connect directly to the database
   file. See [Connecting BI Tools](#connecting-bi-tools) below.

## Project Structure

```
VaRDeck/
├── src/
│   ├── config.py             # Single source of truth: TICKERS, BENCHMARK, RISK_FREE_RATE, DB_URL, ...
│   ├── etl_pipeline.py       # Core data pipeline (prices, returns, risk metrics)
│   ├── risk_analysis.py      # 8-module risk engine (VaR, stress tests, Monte Carlo, ...)
│   └── verify_db.py          # Prints every table + row count + sample, runs sanity checks
├── tests/
│   ├── test_pipeline.py      # Unit tests for etl_pipeline.py
│   └── test_risk_analysis.py # Unit tests for risk_analysis.py
├── docs/
│   ├── data_dictionary.md    # Every column in every table, defined
│   └── risk_methodology.md   # Assumptions behind every risk module
├── .github/workflows/
│   └── daily_refresh.yml     # Scheduled CI: tests + pipeline run on weekdays
├── power_bi/                 # Place your .pbix + DAX notes here
├── tableau/                  # Place your .twbx + calc field notes here
└── requirements.txt
```

## Setup

```bash
git clone <this-repo>
cd VaRDeck

python3 -m venv portfolio_env
source portfolio_env/bin/activate      # Windows: portfolio_env\Scripts\activate

pip install -r requirements.txt
```

## Environment Setup

The pipeline reads its database connection from the `DATABASE_URL`
environment variable. Copy [.env.example](.env.example) and fill in your own
credentials (a hosted Postgres instance such as
[Neon](https://neon.tech) works well — copy the connection string it gives
you, including `?sslmode=require`):

```bash
cp .env.example .env
# edit .env with your real DATABASE_URL, then load it into your shell, e.g.:
export $(grep -v '^#' .env | xargs)
```

`.env` is gitignored and never committed. If `DATABASE_URL` isn't set, the
pipeline falls back to a local SQLite file
(`src/portfolio_dashboard.db`), so the project still works out of the box
for anyone cloning it without a Postgres database.

In CI, `DATABASE_URL` is supplied via a GitHub Actions repository secret
(see `.github/workflows/daily_refresh.yml`) rather than a `.env` file.

## Running the Pipeline

To change the portfolio composition, edit only `src/config.py`.

```bash
cd src
python etl_pipeline.py
```

This fetches live market data, computes all metrics, and writes to whatever
`DATABASE_URL` points at (a hosted Postgres instance, or
`src/portfolio_dashboard.db` via SQLite if unset — see
[Environment Setup](#environment-setup)). It also runs the full risk
analysis engine automatically as the last step.

### Verifying the output

```bash
cd src
python verify_db.py
```

Prints every table's row count and a 3-row sample, then runs sanity checks
(no NaNs, VaR monotonic across confidence levels, CVaR ≥ VaR, Monte Carlo
probabilities in [0,1], risk decomposition sums to portfolio volatility,
etc.).

### Running tests

```bash
python -m pytest tests/ -v
```

26 tests covering both the core pipeline and every risk module.

## Automation

`.github/workflows/daily_refresh.yml` runs the test suite on every push/PR,
and separately runs the full ETL pipeline on a weekday schedule (noon UTC,
~7 AM ET) or on manual trigger. The pipeline's own data quality gate
(`validate_data()` in `etl_pipeline.py`) raises and fails the job loudly if
price data looks wrong (nulls, too few rows, future dates, non-positive
prices, duplicate dates) — see [Known Limitations](#known-limitations) for
what this means for a scheduled SQLite setup specifically.

## Connecting BI Tools

This repo only produces the database — building the actual `.pbix`/`.twbx`
dashboard files requires the respective desktop apps. See
[Final Summary / Manual Steps](#final-summary--manual-steps-for-power-bi--tableau)
below (or ask for it) for exact connection settings.

## Screenshots

_Add Power BI and Tableau dashboard screenshots here once built:_

<!--
### Power BI — Executive Summary
![Power BI Executive Summary](docs/screenshots/powerbi_executive_summary.png)

### Power BI — Risk Analytics
![Power BI Risk Analytics](docs/screenshots/powerbi_risk_analytics.png)

### Tableau — Portfolio Overview
![Tableau Portfolio Overview](docs/screenshots/tableau_overview.png)
-->

## Known Limitations

- **yfinance rate limits**: Yahoo Finance has no official API and can
  rate-limit or intermittently fail multi-ticker batch downloads. The
  pipeline logs failures per-ticker but does not automatically retry.
