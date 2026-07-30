"""
Portfolio Risk Dashboard — ETL Pipeline
Pulls live market data, computes risk metrics, writes to database.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from datetime import datetime, timedelta
import logging

from config import TICKERS, BENCHMARK, RISK_FREE_RATE, LOOKBACK_YEARS, DB_URL, HELD_TICKERS, WEIGHTS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


# ── DATA PULL ────────────────────────────────────────────────────
def fetch_prices(tickers: list, years: int) -> pd.DataFrame:
    """Pull adjusted close prices from Yahoo Finance."""
    end = datetime.today()
    start = end - timedelta(days=years * 365)
    logger.info(f"Fetching {len(tickers)} tickers, {start.date()} → {end.date()}")

    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)

    # yf.download returns MultiIndex columns when multiple tickers
    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"]
    else:
        prices = raw[["Close"]]
        prices.columns = tickers

    prices.index = pd.to_datetime(prices.index)
    prices = prices.ffill().dropna()
    logger.info(f"Got {len(prices)} trading days")
    return prices


# ── DATA QUALITY GATE ────────────────────────────────────────────
def validate_data(prices: pd.DataFrame) -> bool:
    """Data quality gate — stops pipeline if data looks wrong."""
    checks = {
        "no_null_prices":     prices.isnull().sum().sum() == 0,
        "min_rows":           len(prices) > 100,
        "no_future_dates":    prices.index.max() <= pd.Timestamp.today(),
        "reasonable_prices":  (prices > 0).all().all(),
        "no_duplicate_dates": not prices.index.duplicated().any(),
    }
    for name, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        logger.info(f"  {status}: {name}")

    if not all(checks.values()):
        logger.error("DATA QUALITY CHECK FAILED — pipeline halted")
        return False
    return True


# ── RETURN CALCULATIONS ──────────────────────────────────────────
def compute_returns(prices: pd.DataFrame):
    """Daily and cumulative returns."""
    daily = prices.pct_change().dropna()
    cumulative = (1 + daily).cumprod() - 1
    return daily, cumulative


# ── RISK METRICS (per ticker) ────────────────────────────────────
def compute_risk_metrics(daily_returns: pd.DataFrame) -> pd.DataFrame:
    """
    Annualized return, volatility, Sharpe, Sortino, max drawdown,
    VaR (95%), CVaR (95%), Beta vs benchmark.
    """
    trading_days = 252
    metrics = []

    benchmark_returns = daily_returns[BENCHMARK]

    for ticker in daily_returns.columns:
        r = daily_returns[ticker]

        ann_return = r.mean() * trading_days
        ann_vol = r.std() * np.sqrt(trading_days)
        sharpe = (ann_return - RISK_FREE_RATE) / ann_vol if ann_vol != 0 else 0

        # Sortino — only downside deviation
        downside = r[r < 0].std() * np.sqrt(trading_days)
        sortino = (ann_return - RISK_FREE_RATE) / downside if downside != 0 else 0

        # Max Drawdown
        cum = (1 + r).cumprod()
        rolling_max = cum.cummax()
        drawdown = (cum - rolling_max) / rolling_max
        max_dd = drawdown.min()

        # Value at Risk & Conditional VaR (95%)
        var_95 = np.percentile(r, 5)
        cvar_95 = r[r <= var_95].mean()

        # Beta
        if ticker != BENCHMARK:
            cov = np.cov(r, benchmark_returns)[0][1]
            var_bench = benchmark_returns.var()
            beta = cov / var_bench if var_bench != 0 else 0
        else:
            beta = 1.0

        metrics.append({
            "ticker": ticker,
            "sector": TICKERS[ticker]["sector"],
            "weight": TICKERS[ticker]["weight"],
            "annualized_return": round(ann_return, 4),
            "annualized_volatility": round(ann_vol, 4),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "max_drawdown": round(max_dd, 4),
            "var_95": round(var_95, 4),
            "cvar_95": round(cvar_95, 4) if not np.isnan(cvar_95) else 0,
            "beta": round(beta, 4),
        })

    return pd.DataFrame(metrics)


# ── PORTFOLIO-LEVEL METRICS ──────────────────────────────────────
def compute_portfolio_returns(daily_returns: pd.DataFrame) -> pd.Series:
    """Weighted portfolio return series."""
    weights = pd.Series(dict(zip(HELD_TICKERS, WEIGHTS)))
    # Align columns
    common = daily_returns.columns.intersection(weights.index)
    return daily_returns[common].mul(weights[common], axis=1).sum(axis=1)


def compute_correlation_matrix(daily_returns: pd.DataFrame) -> pd.DataFrame:
    """Rolling 90-day correlation, returned as long-format for BI tools."""
    held = HELD_TICKERS
    corr = daily_returns[held].rolling(90).corr()

    # Flatten to long format: date, ticker_a, ticker_b, correlation
    records = []
    for date in corr.index.get_level_values(0).unique():
        try:
            matrix = corr.loc[date]
            for i, a in enumerate(held):
                for j, b in enumerate(held):
                    if j >= i:
                        val = matrix.loc[a, b] if a in matrix.index and b in matrix.columns else np.nan
                        if not np.isnan(val):
                            records.append({
                                "date": date,
                                "ticker_a": a,
                                "ticker_b": b,
                                "correlation": round(val, 4),
                            })
        except (KeyError, TypeError):
            continue

    # Sample to keep table manageable (weekly snapshots)
    df = pd.DataFrame(records)
    if len(df) > 0:
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"].dt.dayofweek == 4]  # Fridays only
    return df


# ── DRAWDOWN SERIES ───────────────────────────────────────────────
def compute_drawdown_series(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily drawdown from peak for each ticker."""
    rolling_max = prices.cummax()
    dd = (prices - rolling_max) / rolling_max
    dd = dd.reset_index().melt(id_vars="Date",
                                var_name="ticker",
                                value_name="drawdown")
    dd.rename(columns={"Date": "date"}, inplace=True)
    return dd


# ── DATABASE WRITE ────────────────────────────────────────────────
def write_to_db(engine, table_name: str, df: pd.DataFrame, if_exists="replace"):
    df.to_sql(table_name, engine, if_exists=if_exists, index=False)
    logger.info(f"Wrote {len(df)} rows → {table_name}")


# ── MAIN PIPELINE ─────────────────────────────────────────────────
def run_pipeline():
    logger.info("=== Pipeline start ===")

    engine = create_engine(DB_URL)
    tickers = list(TICKERS.keys())

    # 1. Fetch prices
    prices = fetch_prices(tickers, LOOKBACK_YEARS)

    # 1b. Data quality gate
    if not validate_data(prices):
        raise ValueError("Data quality checks failed — aborting pipeline run")

    # 2. Compute returns
    daily_ret, cum_ret = compute_returns(prices)

    # 3. Risk metrics table
    risk_metrics = compute_risk_metrics(daily_ret)

    # 4. Portfolio-level daily returns
    port_ret = compute_portfolio_returns(daily_ret)
    port_df = pd.DataFrame({
        "date": port_ret.index,
        "portfolio_return": port_ret.values,
        "cumulative_return": ((1 + port_ret).cumprod() - 1).values,
        "benchmark_return": daily_ret[BENCHMARK].values,
        "benchmark_cumulative": ((1 + daily_ret[BENCHMARK]).cumprod() - 1).values,
    })

    # 5. Correlation matrix (long format)
    corr_df = compute_correlation_matrix(daily_ret)

    # 6. Drawdown series
    dd_df = compute_drawdown_series(prices)

    # 7. Daily prices (long format for BI tools)
    prices_long = prices.reset_index().melt(
        id_vars="Date", var_name="ticker", value_name="close_price"
    )
    prices_long.rename(columns={"Date": "date"}, inplace=True)

    # 8. Metadata / refresh log
    refresh_log = pd.DataFrame([{
        "run_timestamp": datetime.now(),
        "tickers_count": len(tickers),
        "data_start": prices.index.min(),
        "data_end": prices.index.max(),
        "trading_days": len(prices),
    }])

    # 9. Write everything
    write_to_db(engine, "risk_metrics", risk_metrics)
    write_to_db(engine, "portfolio_returns", port_df)
    write_to_db(engine, "correlation_matrix", corr_df)
    write_to_db(engine, "drawdown_series", dd_df)
    write_to_db(engine, "daily_prices", prices_long)
    write_to_db(engine, "refresh_log", refresh_log, if_exists="append")

    logger.info("=== Core pipeline complete ===")

    # 10. Risk analysis engine (VaR, stress tests, Monte Carlo, etc.)
    logger.info("Starting risk analysis engine...")
    from risk_analysis import run_risk_analysis
    risk_results = run_risk_analysis()
    logger.info("Risk analysis complete")

    logger.info("=== Full Pipeline Complete ===")
    return risk_metrics  # for quick verification


if __name__ == "__main__":
    result = run_pipeline()
    print("\n", result.to_string(index=False))
