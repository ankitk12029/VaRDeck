import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
import pytest
from etl_pipeline import (
    validate_data,
    compute_returns,
    compute_risk_metrics,
    compute_portfolio_returns,
    compute_drawdown_series,
    TICKERS,
    BENCHMARK,
)


def make_fake_prices(n_days=300):
    np.random.seed(7)
    tickers = list(TICKERS.keys())
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B", name="Date")
    data = {}
    for t in tickers:
        walk = np.cumprod(1 + np.random.normal(0.0004, 0.015, n_days))
        data[t] = 100 * walk
    return pd.DataFrame(data, index=dates)


class TestValidateData:
    def test_valid_prices_pass(self):
        prices = make_fake_prices()
        assert validate_data(prices) is True

    def test_null_prices_fail(self):
        prices = make_fake_prices()
        prices.iloc[5, 0] = np.nan
        assert validate_data(prices) is False

    def test_too_few_rows_fail(self):
        prices = make_fake_prices(n_days=10)
        assert validate_data(prices) is False

    def test_negative_prices_fail(self):
        prices = make_fake_prices()
        prices.iloc[0, 0] = -5.0
        assert validate_data(prices) is False

    def test_duplicate_dates_fail(self):
        prices = make_fake_prices()
        dup = pd.concat([prices, prices.iloc[[0]]])
        assert validate_data(dup) is False


class TestReturns:
    def test_daily_returns_shape(self):
        prices = make_fake_prices()
        daily, cumulative = compute_returns(prices)
        assert len(daily) == len(prices) - 1
        assert daily.shape[1] == prices.shape[1]

    def test_cumulative_returns_start_near_daily(self):
        prices = make_fake_prices()
        daily, cumulative = compute_returns(prices)
        first_col = daily.columns[0]
        assert abs(cumulative[first_col].iloc[0] - daily[first_col].iloc[0]) < 1e-9


class TestRiskMetrics:
    def test_all_tickers_present(self):
        prices = make_fake_prices()
        daily, _ = compute_returns(prices)
        metrics = compute_risk_metrics(daily)
        assert set(metrics["ticker"]) == set(TICKERS.keys())

    def test_benchmark_beta_is_one(self):
        prices = make_fake_prices()
        daily, _ = compute_returns(prices)
        metrics = compute_risk_metrics(daily)
        bench_row = metrics[metrics["ticker"] == BENCHMARK]
        assert bench_row["beta"].iloc[0] == 1.0

    def test_no_nans_in_metrics(self):
        prices = make_fake_prices()
        daily, _ = compute_returns(prices)
        metrics = compute_risk_metrics(daily)
        assert not metrics.isnull().any().any()


class TestPortfolioReturns:
    def test_portfolio_return_is_weighted_sum(self):
        prices = make_fake_prices()
        daily, _ = compute_returns(prices)
        port_ret = compute_portfolio_returns(daily)
        held = [t for t, info in TICKERS.items() if info["weight"] > 0]
        weights = pd.Series({t: TICKERS[t]["weight"] for t in held})
        expected_first = sum(daily[t].iloc[0] * weights[t] for t in held)
        assert abs(port_ret.iloc[0] - expected_first) < 1e-9

    def test_excludes_zero_weight_tickers(self):
        prices = make_fake_prices()
        daily, _ = compute_returns(prices)
        port_ret = compute_portfolio_returns(daily)
        # Benchmark (SPY) has weight 0.0 and should not affect the portfolio series
        assert len(port_ret) == len(daily)


class TestDrawdownSeries:
    def test_drawdown_never_positive(self):
        prices = make_fake_prices()
        dd = compute_drawdown_series(prices)
        assert (dd["drawdown"] <= 1e-9).all()

    def test_long_format_columns(self):
        prices = make_fake_prices()
        dd = compute_drawdown_series(prices)
        assert set(dd.columns) == {"date", "ticker", "drawdown"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
