import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
import pytest
from risk_analysis import (
    ValueAtRisk, RiskDecomposer, TailRiskAnalyzer,
    ScenarioAnalyzer, EfficientFrontier
)


def make_fake_returns(n_days=500, n_assets=3):
    np.random.seed(42)
    returns = pd.DataFrame(
        np.random.normal(0.0005, 0.02, (n_days, n_assets)),
        columns=["A", "B", "C"]
    )
    return returns


class TestValueAtRisk:
    def test_historical_var_positive(self):
        ret = make_fake_returns()
        weights = np.array([0.5, 0.3, 0.2])
        var = ValueAtRisk(ret, weights)
        result = var.historical_var()
        assert (result["var_dollar"] > 0).all()

    def test_cvar_exceeds_var(self):
        """CVaR should always be >= VaR (it's the avg loss beyond VaR)."""
        ret = make_fake_returns()
        weights = np.array([0.5, 0.3, 0.2])
        var = ValueAtRisk(ret, weights)
        result = var.run_all()
        for _, row in result.iterrows():
            assert row["cvar_dollar"] >= row["var_dollar"] * 0.95  # allow small float error

    def test_higher_confidence_higher_var(self):
        """99% VaR should exceed 90% VaR."""
        ret = make_fake_returns()
        weights = np.array([0.5, 0.3, 0.2])
        var = ValueAtRisk(ret, weights)
        result = var.historical_var()
        var_90 = result[result["confidence_level"] == 0.90]["var_dollar"].values[0]
        var_99 = result[result["confidence_level"] == 0.99]["var_dollar"].values[0]
        assert var_99 > var_90

    def test_monte_carlo_var_no_nans(self):
        ret = make_fake_returns()
        weights = np.array([0.5, 0.3, 0.2])
        var = ValueAtRisk(ret, weights)
        result = var.monte_carlo_var(simulations=1000)
        assert not result.isnull().any().any()
        assert (result["var_dollar"] >= 0).all()


class TestRiskDecomposition:
    def test_contributions_sum_to_portfolio_vol(self):
        ret = make_fake_returns()
        weights = np.array([0.5, 0.3, 0.2])
        decomp = RiskDecomposer(ret, weights, ["A", "B", "C"])
        result = decomp.marginal_risk_contribution()
        total = result["component_risk"].sum()
        port_vol = result["portfolio_volatility"].iloc[0]
        assert abs(total - port_vol) < 0.001

    def test_diversification_ratio_above_one(self):
        ret = make_fake_returns()
        weights = np.array([0.5, 0.3, 0.2])
        decomp = RiskDecomposer(ret, weights, ["A", "B", "C"])
        ratio = decomp.diversification_ratio()
        assert ratio >= 1.0


class TestTailRisk:
    def test_kurtosis_computed(self):
        ret = make_fake_returns()
        port_ret = ret.dot(np.array([0.5, 0.3, 0.2]))
        tail = TailRiskAnalyzer(port_ret)
        stats = tail.compute_tail_stats()
        assert "kurtosis" in stats
        assert "skewness" in stats

    def test_worst_days_count(self):
        ret = make_fake_returns()
        port_ret = ret.dot(np.array([0.5, 0.3, 0.2]))
        tail = TailRiskAnalyzer(port_ret)
        worst_best = tail.worst_days_table(n=10)
        assert len(worst_best) == 20  # 10 worst + 10 best


class TestScenarioAnalyzer:
    def test_scenario_rows_include_portfolio_total(self):
        tickers_config = {
            "AAPL": {"sector": "Technology", "weight": 0.5},
            "JPM":  {"sector": "Financials", "weight": 0.5},
        }
        scenarios = ScenarioAnalyzer(tickers_config)
        result = scenarios.run_scenarios()
        assert (result["ticker"] == "PORTFOLIO").sum() == len(scenarios.HYPOTHETICAL_SCENARIOS)
        assert not result.isnull().any().any()

    def test_weighted_impact_matches_shock_times_weight(self):
        tickers_config = {"AAPL": {"sector": "Technology", "weight": 1.0}}
        scenarios = ScenarioAnalyzer(tickers_config)
        result = scenarios.run_scenarios()
        rate_spike = result[(result["scenario"] == "Rate Spike (+200bps)") &
                             (result["ticker"] == "AAPL")]
        expected = scenarios.HYPOTHETICAL_SCENARIOS["Rate Spike (+200bps)"]["Technology"] * 1.0
        assert abs(rate_spike["weighted_impact"].iloc[0] - expected) < 1e-6


class TestEfficientFrontier:
    def test_optimal_sharpe_exceeds_equal_weight(self):
        ret = make_fake_returns()
        equal_w = np.array([1/3, 1/3, 1/3])
        ef = EfficientFrontier(ret, ["A", "B", "C"], equal_w, risk_free_rate=0.05)
        optimal = ef.find_optimal_portfolio()
        assert optimal["optimal_sharpe"] >= optimal["current_sharpe"] - 0.01

    def test_optimal_weights_sum_to_one(self):
        ret = make_fake_returns()
        equal_w = np.array([1/3, 1/3, 1/3])
        ef = EfficientFrontier(ret, ["A", "B", "C"], equal_w, risk_free_rate=0.05)
        optimal = ef.find_optimal_portfolio()
        total_weight = sum(optimal["optimal_weights"].values())
        assert abs(total_weight - 1.0) < 1e-4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
