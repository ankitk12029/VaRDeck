"""
Portfolio Risk Analysis Engine
==============================
Advanced risk analytics module that plugs into the main ETL pipeline.
Covers: VaR (3 methods), stress testing, scenario analysis, risk decomposition,
        tail risk, rolling risk regime detection, and factor exposure.

All outputs write to the same database as the main pipeline.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from scipy import stats
from scipy.optimize import minimize
from sqlalchemy import create_engine
from datetime import datetime, timedelta
import warnings
import logging

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ── CONFIGURATION (import from main pipeline or duplicate here) ────────
TICKERS = {
    "AAPL":  {"sector": "Technology",    "weight": 0.20},
    "MSFT":  {"sector": "Technology",    "weight": 0.15},
    "JPM":   {"sector": "Financials",    "weight": 0.15},
    "JNJ":   {"sector": "Healthcare",    "weight": 0.10},
    "XOM":   {"sector": "Energy",        "weight": 0.10},
    "PG":    {"sector": "Consumer",      "weight": 0.10},
    "SPY":   {"sector": "Benchmark",     "weight": 0.00},
    "BND":   {"sector": "Fixed Income",  "weight": 0.10},
    "GLD":   {"sector": "Commodities",   "weight": 0.10},
}

BENCHMARK = "SPY"
RISK_FREE_RATE = 0.05
DB_URL = "sqlite:///portfolio_dashboard.db"

HELD_TICKERS = [t for t, info in TICKERS.items() if info["weight"] > 0]
WEIGHTS = np.array([TICKERS[t]["weight"] for t in HELD_TICKERS])


# ======================================================================
# MODULE 1: VALUE AT RISK — THREE METHODS
# ======================================================================

class ValueAtRisk:
    """
    Computes VaR using three industry-standard methods.
    Interviewers love asking "which VaR method and why?"
    Having all three with a comparison table is a strong answer.
    """

    def __init__(self, returns: pd.DataFrame, weights: np.ndarray,
                 confidence_levels: list = [0.90, 0.95, 0.99],
                 portfolio_value: float = 100000):
        self.returns = returns
        self.weights = weights
        self.confidence_levels = confidence_levels
        self.portfolio_value = portfolio_value
        self.portfolio_returns = returns.dot(weights)

    def historical_var(self) -> pd.DataFrame:
        """
        Historical Simulation VaR
        No distribution assumptions — uses actual return percentiles.
        """
        results = []
        for cl in self.confidence_levels:
            alpha = 1 - cl
            var_pct = np.percentile(self.portfolio_returns, alpha * 100)
            var_dollar = abs(var_pct) * self.portfolio_value

            # Conditional VaR (Expected Shortfall)
            tail = self.portfolio_returns[self.portfolio_returns <= var_pct]
            cvar_pct = tail.mean() if len(tail) > 0 else var_pct
            cvar_dollar = abs(cvar_pct) * self.portfolio_value

            results.append({
                "method": "Historical Simulation",
                "confidence_level": cl,
                "var_pct": round(abs(var_pct), 6),
                "var_dollar": round(var_dollar, 2),
                "cvar_pct": round(abs(cvar_pct), 6),
                "cvar_dollar": round(cvar_dollar, 2),
            })
        return pd.DataFrame(results)

    def parametric_var(self) -> pd.DataFrame:
        """
        Variance-Covariance (Parametric) VaR
        Assumes normal distribution — fast but underestimates tail risk.
        """
        port_mean = self.portfolio_returns.mean()
        port_std = self.portfolio_returns.std()
        results = []

        for cl in self.confidence_levels:
            z_score = stats.norm.ppf(1 - cl)
            var_pct = -(port_mean + z_score * port_std)
            var_dollar = var_pct * self.portfolio_value

            # Parametric CVaR
            cvar_pct = -(port_mean - port_std * stats.norm.pdf(z_score) / (1 - cl))
            cvar_dollar = cvar_pct * self.portfolio_value

            results.append({
                "method": "Parametric (Normal)",
                "confidence_level": cl,
                "var_pct": round(var_pct, 6),
                "var_dollar": round(var_dollar, 2),
                "cvar_pct": round(abs(cvar_pct), 6),
                "cvar_dollar": round(cvar_dollar, 2),
            })
        return pd.DataFrame(results)

    def monte_carlo_var(self, simulations: int = 10000,
                        horizon_days: int = 1) -> pd.DataFrame:
        """
        Monte Carlo VaR
        Simulates correlated returns using Cholesky decomposition.
        Most robust method — handles non-linear instruments.
        """
        # Covariance matrix for correlated simulation
        cov_matrix = self.returns.cov().values
        mean_returns = self.returns.mean().values

        # Cholesky decomposition for correlated random draws
        try:
            cholesky = np.linalg.cholesky(cov_matrix)
        except np.linalg.LinAlgError:
            # If not positive definite, use nearest PD matrix
            eigenvalues, eigenvectors = np.linalg.eigh(cov_matrix)
            eigenvalues = np.maximum(eigenvalues, 1e-8)
            cov_matrix = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
            cholesky = np.linalg.cholesky(cov_matrix)

        simulated_returns = []
        for _ in range(simulations):
            z = np.random.standard_normal(len(mean_returns))
            correlated_returns = mean_returns * horizon_days + \
                                 cholesky @ z * np.sqrt(horizon_days)
            port_return = np.dot(self.weights, correlated_returns)
            simulated_returns.append(port_return)

        simulated_returns = np.array(simulated_returns)
        results = []

        for cl in self.confidence_levels:
            alpha = 1 - cl
            var_pct = abs(np.percentile(simulated_returns, alpha * 100))
            var_dollar = var_pct * self.portfolio_value

            tail = simulated_returns[simulated_returns <=
                                     np.percentile(simulated_returns, alpha * 100)]
            cvar_pct = abs(tail.mean()) if len(tail) > 0 else var_pct
            cvar_dollar = cvar_pct * self.portfolio_value

            results.append({
                "method": "Monte Carlo",
                "confidence_level": cl,
                "var_pct": round(var_pct, 6),
                "var_dollar": round(var_dollar, 2),
                "cvar_pct": round(cvar_pct, 6),
                "cvar_dollar": round(cvar_dollar, 2),
            })

        return pd.DataFrame(results)

    def run_all(self) -> pd.DataFrame:
        """Run all three VaR methods and combine."""
        hist = self.historical_var()
        param = self.parametric_var()
        mc = self.monte_carlo_var()
        combined = pd.concat([hist, param, mc], ignore_index=True)
        combined["portfolio_value"] = self.portfolio_value
        combined["calculation_date"] = datetime.now().date()
        logger.info(f"VaR computed: {len(combined)} rows (3 methods x {len(self.confidence_levels)} CLs)")
        return combined


# ======================================================================
# MODULE 2: STRESS TESTING — HISTORICAL SCENARIOS
# ======================================================================

class StressTester:
    """
    Replays portfolio through actual historical crises.
    Shows: "If 2008 happened again, here's what this portfolio would lose."
    """

    # Major market events with approximate date ranges
    SCENARIOS = {
        "2008 Financial Crisis":       ("2008-09-01", "2009-03-09"),
        "2011 EU Debt Crisis":         ("2011-07-01", "2011-10-03"),
        "2015 China Slowdown":         ("2015-08-10", "2015-08-25"),
        "2018 Q4 Selloff":            ("2018-10-01", "2018-12-24"),
        "COVID-19 Crash":             ("2020-02-19", "2020-03-23"),
        "2022 Rate Hike Selloff":     ("2022-01-03", "2022-10-12"),
    }

    def __init__(self, tickers: list, weights: np.ndarray):
        self.tickers = tickers
        self.weights = weights

    def run_stress_tests(self) -> pd.DataFrame:
        """Pull historical data for each crisis period and compute impact."""
        results = []

        for scenario_name, (start, end) in self.SCENARIOS.items():
            try:
                prices = yf.download(self.tickers, start=start, end=end,
                                     auto_adjust=True, progress=False)
                if isinstance(prices.columns, pd.MultiIndex):
                    close = prices["Close"]
                else:
                    close = prices

                if close.empty or len(close) < 2:
                    logger.warning(f"Skipping {scenario_name}: insufficient data")
                    continue

                close = close.ffill().dropna(axis=1)
                available = [t for t in self.tickers if t in close.columns]
                if len(available) < len(self.tickers):
                    logger.warning(f"{scenario_name}: only {len(available)}/{len(self.tickers)} tickers available")

                # Period returns
                period_returns = (close.iloc[-1] / close.iloc[0]) - 1

                for i, ticker in enumerate(available):
                    idx = self.tickers.index(ticker)
                    ticker_return = period_returns[ticker]
                    weighted_impact = ticker_return * self.weights[idx]

                    results.append({
                        "scenario": scenario_name,
                        "period_start": start,
                        "period_end": end,
                        "trading_days": len(close),
                        "ticker": ticker,
                        "ticker_return": round(ticker_return, 4),
                        "weight": self.weights[idx],
                        "weighted_impact": round(weighted_impact, 4),
                    })

            except Exception as e:
                logger.error(f"Error in {scenario_name}: {e}")
                continue

        df = pd.DataFrame(results)

        # Add portfolio-level summary
        if len(df) > 0:
            portfolio_summary = df.groupby("scenario").agg(
                portfolio_impact=("weighted_impact", "sum"),
                worst_ticker_return=("ticker_return", "min"),
                best_ticker_return=("ticker_return", "max"),
                period_start=("period_start", "first"),
                period_end=("period_end", "first"),
                trading_days=("trading_days", "first"),
            ).reset_index()
            portfolio_summary["ticker"] = "PORTFOLIO"
            portfolio_summary["ticker_return"] = portfolio_summary["portfolio_impact"]
            portfolio_summary["weight"] = 1.0
            portfolio_summary["weighted_impact"] = portfolio_summary["portfolio_impact"]

        logger.info(f"Stress tests complete: {len(df)} ticker-scenario rows")
        return df, portfolio_summary


# ======================================================================
# MODULE 3: HYPOTHETICAL SCENARIO ANALYSIS
# ======================================================================

class ScenarioAnalyzer:
    """
    What-if scenarios: "What if rates spike 2%?" / "What if tech drops 30%?"
    Uses sector-level shocks applied to current weights.
    """

    HYPOTHETICAL_SCENARIOS = {
        "Rate Spike (+200bps)": {
            "Technology": -0.15, "Financials": 0.05, "Healthcare": -0.05,
            "Energy": -0.03, "Consumer": -0.08, "Fixed Income": -0.12,
            "Commodities": -0.02,
        },
        "Tech Bubble Burst": {
            "Technology": -0.40, "Financials": -0.10, "Healthcare": 0.02,
            "Energy": -0.05, "Consumer": -0.08, "Fixed Income": 0.05,
            "Commodities": 0.03,
        },
        "Oil Shock ($150/bbl)": {
            "Technology": -0.08, "Financials": -0.05, "Healthcare": -0.03,
            "Energy": 0.35, "Consumer": -0.12, "Fixed Income": -0.05,
            "Commodities": 0.15,
        },
        "Global Recession": {
            "Technology": -0.25, "Financials": -0.30, "Healthcare": -0.08,
            "Energy": -0.20, "Consumer": -0.15, "Fixed Income": 0.08,
            "Commodities": -0.10,
        },
        "Inflation Surge (+5%)": {
            "Technology": -0.12, "Financials": 0.03, "Healthcare": -0.05,
            "Energy": 0.10, "Consumer": -0.10, "Fixed Income": -0.15,
            "Commodities": 0.20,
        },
        "Bull Market Rally": {
            "Technology": 0.25, "Financials": 0.15, "Healthcare": 0.12,
            "Energy": 0.10, "Consumer": 0.15, "Fixed Income": 0.02,
            "Commodities": 0.05,
        },
    }

    def __init__(self, tickers_config: dict):
        self.tickers_config = tickers_config

    def run_scenarios(self, portfolio_value: float = 100000) -> pd.DataFrame:
        """Apply each hypothetical shock and compute portfolio impact."""
        results = []

        for scenario_name, sector_shocks in self.HYPOTHETICAL_SCENARIOS.items():
            portfolio_impact = 0

            for ticker, info in self.tickers_config.items():
                if info["weight"] == 0:
                    continue

                sector = info["sector"]
                shock = sector_shocks.get(sector, 0)
                weighted_impact = shock * info["weight"]
                portfolio_impact += weighted_impact

                results.append({
                    "scenario": scenario_name,
                    "scenario_type": "Hypothetical",
                    "ticker": ticker,
                    "sector": sector,
                    "weight": info["weight"],
                    "sector_shock": shock,
                    "weighted_impact": round(weighted_impact, 4),
                    "dollar_impact": round(weighted_impact * portfolio_value, 2),
                })

            # Portfolio total row
            results.append({
                "scenario": scenario_name,
                "scenario_type": "Hypothetical",
                "ticker": "PORTFOLIO",
                "sector": "ALL",
                "weight": 1.0,
                "sector_shock": portfolio_impact,
                "weighted_impact": round(portfolio_impact, 4),
                "dollar_impact": round(portfolio_impact * portfolio_value, 2),
            })

        logger.info(f"Scenario analysis: {len(results)} rows across "
                     f"{len(self.HYPOTHETICAL_SCENARIOS)} scenarios")
        return pd.DataFrame(results)


# ======================================================================
# MODULE 4: RISK CONTRIBUTION & DECOMPOSITION
# ======================================================================

class RiskDecomposer:
    """
    Answers: "Which positions are ACTUALLY driving portfolio risk?"
    Weight != risk contribution. A 10% position in a volatile,
    highly-correlated asset can contribute 30%+ of total risk.
    """

    def __init__(self, returns: pd.DataFrame, weights: np.ndarray,
                 tickers: list):
        self.returns = returns
        self.weights = weights
        self.tickers = tickers
        self.cov_matrix = returns.cov().values * 252  # annualized

    def marginal_risk_contribution(self) -> pd.DataFrame:
        """
        Marginal Contribution to Risk (MCR) and Component VaR.
        MCR_i = (Sigma * w)_i / sigma_p
        """
        portfolio_var = self.weights @ self.cov_matrix @ self.weights
        portfolio_vol = np.sqrt(portfolio_var)

        # Marginal contribution: partial derivative of portfolio vol w.r.t. weight
        marginal = (self.cov_matrix @ self.weights) / portfolio_vol

        # Component risk: weight * marginal contribution
        component = self.weights * marginal

        # Percentage contribution
        pct_contribution = component / portfolio_vol

        results = []
        for i, ticker in enumerate(self.tickers):
            results.append({
                "ticker": ticker,
                "weight": round(self.weights[i], 4),
                "standalone_vol": round(np.sqrt(self.cov_matrix[i][i]), 4),
                "marginal_contribution": round(marginal[i], 6),
                "component_risk": round(component[i], 6),
                "pct_of_total_risk": round(pct_contribution[i], 4),
                "risk_weight_ratio": round(pct_contribution[i] / self.weights[i], 2)
                    if self.weights[i] > 0 else 0,
            })

        df = pd.DataFrame(results)
        df["portfolio_volatility"] = round(portfolio_vol, 4)
        logger.info(f"Risk decomposition complete: portfolio vol = {portfolio_vol:.4f}")
        return df

    def diversification_ratio(self) -> float:
        """
        Diversification Ratio = weighted avg standalone vol / portfolio vol.
        > 1 means diversification is working. Higher = better.
        """
        standalone_vols = np.sqrt(np.diag(self.cov_matrix))
        weighted_avg_vol = np.dot(self.weights, standalone_vols)
        portfolio_vol = np.sqrt(self.weights @ self.cov_matrix @ self.weights)
        ratio = weighted_avg_vol / portfolio_vol
        logger.info(f"Diversification ratio: {ratio:.2f}")
        return round(ratio, 4)


# ======================================================================
# MODULE 5: ROLLING RISK REGIME DETECTION
# ======================================================================

class RiskRegimeDetector:
    """
    Classifies each trading day into a risk regime based on
    rolling volatility: Low / Normal / Elevated / High / Crisis.
    Useful for conditional analysis: "How does Sharpe behave
    in high-vol regimes?"
    """

    REGIMES = {
        "Low":      (0.00, 0.10),
        "Normal":   (0.10, 0.18),
        "Elevated": (0.18, 0.25),
        "High":     (0.25, 0.35),
        "Crisis":   (0.35, 999),
    }

    def __init__(self, portfolio_returns: pd.Series, window: int = 21):
        self.returns = portfolio_returns
        self.window = window

    def detect_regimes(self) -> pd.DataFrame:
        """Compute rolling vol and assign regime labels."""
        rolling_vol = self.returns.rolling(self.window).std() * np.sqrt(252)
        rolling_vol = rolling_vol.dropna()

        regime_labels = []
        for vol in rolling_vol:
            label = "Normal"
            for regime_name, (low, high) in self.REGIMES.items():
                if low <= vol < high:
                    label = regime_name
                    break
            regime_labels.append(label)

        df = pd.DataFrame({
            "date": rolling_vol.index,
            "rolling_vol": rolling_vol.values.round(4),
            "regime": regime_labels,
        })

        # Add regime-level stats
        regime_stats = []
        for regime in self.REGIMES:
            mask = df["regime"] == regime
            if mask.sum() > 0:
                regime_returns = self.returns.loc[df.loc[mask, "date"]]
                regime_stats.append({
                    "regime": regime,
                    "days_count": mask.sum(),
                    "pct_of_time": round(mask.sum() / len(df), 4),
                    "avg_daily_return": round(regime_returns.mean(), 6),
                    "annualized_return": round(regime_returns.mean() * 252, 4),
                    "avg_volatility": round(df.loc[mask, "rolling_vol"].mean(), 4),
                    "sharpe_in_regime": round(
                        (regime_returns.mean() * 252 - RISK_FREE_RATE) /
                        (regime_returns.std() * np.sqrt(252)), 4
                    ) if regime_returns.std() > 0 else 0,
                })

        logger.info(f"Regime detection: {len(df)} days classified")
        return df, pd.DataFrame(regime_stats)


# ======================================================================
# MODULE 6: TAIL RISK ANALYSIS
# ======================================================================

class TailRiskAnalyzer:
    """
    Deep dive into extreme return days.
    Answers: "How fat are the tails?" and "Is the portfolio
    skewed positively or negatively?"
    """

    def __init__(self, portfolio_returns: pd.Series):
        self.returns = portfolio_returns

    def compute_tail_stats(self) -> dict:
        """Distributional properties and tail metrics."""
        r = self.returns

        return {
            "mean_daily": round(r.mean(), 6),
            "std_daily": round(r.std(), 6),
            "skewness": round(stats.skew(r), 4),
            "kurtosis": round(stats.kurtosis(r), 4),   # excess kurtosis
            "jarque_bera_stat": round(stats.jarque_bera(r)[0], 2),
            "jarque_bera_pvalue": round(stats.jarque_bera(r)[1], 6),
            "is_normal": "No" if stats.jarque_bera(r)[1] < 0.05 else "Yes",
            "worst_day": round(r.min(), 4),
            "best_day": round(r.max(), 4),
            "pct_negative_days": round((r < 0).mean(), 4),
            "pct_beyond_2sigma": round(
                ((r < r.mean() - 2*r.std()) | (r > r.mean() + 2*r.std())).mean(), 4
            ),
            "pct_beyond_3sigma": round(
                ((r < r.mean() - 3*r.std()) | (r > r.mean() + 3*r.std())).mean(), 4
            ),
            "expected_2sigma_pct": 0.0455,   # normal distribution baseline
            "expected_3sigma_pct": 0.0027,
        }

    def worst_days_table(self, n: int = 20) -> pd.DataFrame:
        """Top N worst and best days for storytelling."""
        sorted_returns = self.returns.sort_values()

        worst = sorted_returns.head(n).reset_index()
        worst.columns = ["date", "return"]
        worst["rank"] = range(1, n + 1)
        worst["type"] = "Worst"

        best = sorted_returns.tail(n).sort_values(ascending=False).reset_index()
        best.columns = ["date", "return"]
        best["rank"] = range(1, n + 1)
        best["type"] = "Best"

        df = pd.concat([worst, best], ignore_index=True)
        df["return"] = df["return"].round(4)
        return df

    def return_distribution_bins(self, bins: int = 50) -> pd.DataFrame:
        """Histogram data for plotting return distribution in BI tools."""
        counts, edges = np.histogram(self.returns, bins=bins)
        centers = (edges[:-1] + edges[1:]) / 2

        # Overlay normal distribution for comparison
        normal_density = stats.norm.pdf(centers, self.returns.mean(), self.returns.std())
        normal_counts = normal_density * len(self.returns) * (edges[1] - edges[0])

        return pd.DataFrame({
            "bin_center": centers.round(4),
            "actual_count": counts,
            "normal_expected": normal_counts.round(1),
            "excess": (counts - normal_counts).round(1),
        })


# ======================================================================
# MODULE 7: EFFICIENT FRONTIER & OPTIMAL PORTFOLIO
# ======================================================================

class EfficientFrontier:
    """
    Compute the efficient frontier and find the optimal portfolio.
    Shows where the current portfolio sits vs. the theoretical optimum.
    """

    def __init__(self, returns: pd.DataFrame, tickers: list,
                 current_weights: np.ndarray, risk_free_rate: float = 0.05):
        self.returns = returns
        self.tickers = tickers
        self.current_weights = current_weights
        self.rf = risk_free_rate
        self.mean_returns = returns.mean() * 252
        self.cov_matrix = returns.cov() * 252
        self.n_assets = len(tickers)

    def _portfolio_performance(self, weights):
        ret = np.dot(weights, self.mean_returns)
        vol = np.sqrt(weights @ self.cov_matrix.values @ weights)
        return ret, vol

    def _neg_sharpe(self, weights):
        ret, vol = self._portfolio_performance(weights)
        return -(ret - self.rf) / vol

    def find_optimal_portfolio(self) -> dict:
        """Find the maximum Sharpe ratio portfolio."""
        constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1}
        bounds = tuple((0.0, 0.40) for _ in range(self.n_assets))
        init = np.array([1.0 / self.n_assets] * self.n_assets)

        result = minimize(self._neg_sharpe, init, method="SLSQP",
                          bounds=bounds, constraints=constraints)

        opt_ret, opt_vol = self._portfolio_performance(result.x)
        cur_ret, cur_vol = self._portfolio_performance(self.current_weights)

        optimal = {
            "optimal_weights": {self.tickers[i]: round(result.x[i], 4)
                                for i in range(self.n_assets)},
            "optimal_return": round(opt_ret, 4),
            "optimal_volatility": round(opt_vol, 4),
            "optimal_sharpe": round((opt_ret - self.rf) / opt_vol, 4),
            "current_return": round(cur_ret, 4),
            "current_volatility": round(cur_vol, 4),
            "current_sharpe": round((cur_ret - self.rf) / cur_vol, 4),
        }
        logger.info(f"Optimal Sharpe: {optimal['optimal_sharpe']} vs "
                     f"Current: {optimal['current_sharpe']}")
        return optimal

    def compute_frontier(self, n_points: int = 50) -> pd.DataFrame:
        """Generate the efficient frontier curve."""
        target_returns = np.linspace(
            self.mean_returns.min(), self.mean_returns.max(), n_points
        )
        frontier = []

        for target in target_returns:
            constraints = [
                {"type": "eq", "fun": lambda w: np.sum(w) - 1},
                {"type": "eq", "fun": lambda w, t=target: np.dot(w, self.mean_returns) - t},
            ]
            bounds = tuple((0.0, 0.40) for _ in range(self.n_assets))
            init = np.array([1.0 / self.n_assets] * self.n_assets)

            try:
                result = minimize(
                    lambda w: np.sqrt(w @ self.cov_matrix.values @ w),
                    init, method="SLSQP", bounds=bounds, constraints=constraints
                )
                if result.success:
                    ret, vol = self._portfolio_performance(result.x)
                    frontier.append({
                        "target_return": round(ret, 4),
                        "min_volatility": round(vol, 4),
                        "sharpe": round((ret - self.rf) / vol, 4) if vol > 0 else 0,
                        "point_type": "frontier",
                    })
            except Exception:
                continue

        # Add current portfolio point
        cur_ret, cur_vol = self._portfolio_performance(self.current_weights)
        frontier.append({
            "target_return": round(cur_ret, 4),
            "min_volatility": round(cur_vol, 4),
            "sharpe": round((cur_ret - self.rf) / cur_vol, 4),
            "point_type": "current_portfolio",
        })

        # Add individual asset points
        for i, ticker in enumerate(self.tickers):
            w = np.zeros(self.n_assets)
            w[i] = 1.0
            ret, vol = self._portfolio_performance(w)
            frontier.append({
                "target_return": round(ret, 4),
                "min_volatility": round(vol, 4),
                "sharpe": round((ret - self.rf) / vol, 4) if vol > 0 else 0,
                "point_type": f"asset_{ticker}",
            })

        logger.info(f"Efficient frontier: {len(frontier)} points")
        return pd.DataFrame(frontier)


# ======================================================================
# MODULE 8: MONTE CARLO SIMULATION (FULL PATHS)
# ======================================================================

class MonteCarloSimulator:
    """
    Full path simulation for forward-looking risk assessment.
    Stores percentile bands for visualization.
    """

    def __init__(self, portfolio_returns: pd.Series, portfolio_value: float = 100000):
        self.returns = portfolio_returns
        self.value = portfolio_value
        self.mu = portfolio_returns.mean()
        self.sigma = portfolio_returns.std()

    def simulate(self, days: int = 252, n_sims: int = 5000) -> pd.DataFrame:
        """Run simulations and return percentile bands."""
        all_paths = np.zeros((n_sims, days + 1))
        all_paths[:, 0] = self.value

        for t in range(1, days + 1):
            z = np.random.standard_normal(n_sims)
            daily_return = self.mu + self.sigma * z
            all_paths[:, t] = all_paths[:, t-1] * (1 + daily_return)

        # Extract percentile bands
        percentiles = [5, 10, 25, 50, 75, 90, 95]
        records = []
        for day in range(days + 1):
            row = {"day": day}
            for p in percentiles:
                row[f"p{p}"] = round(np.percentile(all_paths[:, day], p), 2)
            row["mean"] = round(all_paths[:, day].mean(), 2)
            records.append(row)

        # Final distribution stats
        final_values = all_paths[:, -1]
        summary = {
            "median_final": round(np.median(final_values), 2),
            "mean_final": round(final_values.mean(), 2),
            "p5_final": round(np.percentile(final_values, 5), 2),
            "p95_final": round(np.percentile(final_values, 95), 2),
            "prob_loss": round((final_values < self.value).mean(), 4),
            "prob_gain_10pct": round((final_values > self.value * 1.10).mean(), 4),
            "prob_loss_10pct": round((final_values < self.value * 0.90).mean(), 4),
            "expected_shortfall_5pct": round(
                final_values[final_values <= np.percentile(final_values, 5)].mean(), 2
            ),
        }

        logger.info(f"Monte Carlo: {n_sims} simulations, {days} days. "
                     f"Median final: ${summary['median_final']:,.0f}")
        return pd.DataFrame(records), summary


# ======================================================================
# MAIN: RUN ALL RISK ANALYSIS AND WRITE TO DB
# ======================================================================

def run_risk_analysis():
    """Execute all risk modules and write results to database."""
    logger.info("=" * 60)
    logger.info("RISK ANALYSIS ENGINE — START")
    logger.info("=" * 60)

    engine = create_engine(DB_URL)

    # ── Fetch price data ──
    end = datetime.today()
    start = end - timedelta(days=3 * 365)
    tickers = list(TICKERS.keys())

    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"]
    else:
        prices = raw[["Close"]]
        prices.columns = tickers
    prices = prices.ffill().dropna()

    daily_returns = prices.pct_change().dropna()
    held_returns = daily_returns[HELD_TICKERS]

    # Portfolio-level returns
    port_returns = held_returns.dot(WEIGHTS)

    # ── MODULE 1: Value at Risk ──
    logger.info("Module 1: Value at Risk (3 methods)")
    var_engine = ValueAtRisk(held_returns, WEIGHTS)
    var_results = var_engine.run_all()
    var_results.to_sql("var_analysis", engine, if_exists="replace", index=False)

    # ── MODULE 2: Stress Testing ──
    logger.info("Module 2: Historical Stress Testing")
    stress = StressTester(HELD_TICKERS, WEIGHTS)
    stress_detail, stress_summary = stress.run_stress_tests()
    stress_detail.to_sql("stress_test_detail", engine, if_exists="replace", index=False)
    stress_summary.to_sql("stress_test_summary", engine, if_exists="replace", index=False)

    # ── MODULE 3: Scenario Analysis ──
    logger.info("Module 3: Hypothetical Scenario Analysis")
    scenarios = ScenarioAnalyzer(TICKERS)
    scenario_results = scenarios.run_scenarios()
    scenario_results.to_sql("scenario_analysis", engine, if_exists="replace", index=False)

    # ── MODULE 4: Risk Decomposition ──
    logger.info("Module 4: Risk Contribution Decomposition")
    decomposer = RiskDecomposer(held_returns, WEIGHTS, HELD_TICKERS)
    risk_contrib = decomposer.marginal_risk_contribution()
    div_ratio = decomposer.diversification_ratio()
    risk_contrib.to_sql("risk_decomposition", engine, if_exists="replace", index=False)

    # ── MODULE 5: Risk Regimes ──
    logger.info("Module 5: Risk Regime Detection")
    regime_detector = RiskRegimeDetector(port_returns)
    regime_daily, regime_stats = regime_detector.detect_regimes()
    regime_daily.to_sql("risk_regimes_daily", engine, if_exists="replace", index=False)
    regime_stats.to_sql("risk_regime_stats", engine, if_exists="replace", index=False)

    # ── MODULE 6: Tail Risk ──
    logger.info("Module 6: Tail Risk Analysis")
    tail = TailRiskAnalyzer(port_returns)
    tail_stats = tail.compute_tail_stats()
    worst_best = tail.worst_days_table()
    distribution = tail.return_distribution_bins()
    pd.DataFrame([tail_stats]).to_sql("tail_risk_stats", engine,
                                       if_exists="replace", index=False)
    worst_best.to_sql("extreme_days", engine, if_exists="replace", index=False)
    distribution.to_sql("return_distribution", engine, if_exists="replace", index=False)

    # ── MODULE 7: Efficient Frontier ──
    logger.info("Module 7: Efficient Frontier")
    ef = EfficientFrontier(held_returns, HELD_TICKERS, WEIGHTS)
    optimal = ef.find_optimal_portfolio()
    frontier = ef.compute_frontier()
    frontier.to_sql("efficient_frontier", engine, if_exists="replace", index=False)

    # optimal_weights is a nested ticker->weight dict; SQLite columns can't
    # hold a dict, so it's written to its own long-format table instead.
    optimal_weights_df = pd.DataFrame(
        list(optimal["optimal_weights"].items()), columns=["ticker", "weight"]
    )
    optimal_weights_df.to_sql("optimal_weights", engine, if_exists="replace", index=False)

    optimal_summary = {k: v for k, v in optimal.items() if k != "optimal_weights"}
    pd.DataFrame([optimal_summary]).to_sql("optimal_portfolio", engine,
                                    if_exists="replace", index=False)

    # ── MODULE 8: Monte Carlo ──
    logger.info("Module 8: Monte Carlo Simulation")
    mc = MonteCarloSimulator(port_returns)
    mc_paths, mc_summary = mc.simulate()
    mc_paths.to_sql("monte_carlo_paths", engine, if_exists="replace", index=False)
    pd.DataFrame([mc_summary]).to_sql("monte_carlo_summary", engine,
                                       if_exists="replace", index=False)

    # ── Metadata ──
    meta = pd.DataFrame([{
        "run_timestamp": datetime.now(),
        "diversification_ratio": div_ratio,
        "modules_run": 8,
        "tickers_analyzed": len(HELD_TICKERS),
    }])
    meta.to_sql("risk_analysis_log", engine, if_exists="append", index=False)

    logger.info("=" * 60)
    logger.info("RISK ANALYSIS ENGINE — COMPLETE")
    logger.info(f"  Tables written: 14")
    logger.info(f"  Diversification Ratio: {div_ratio}")
    logger.info(f"  Optimal Sharpe: {optimal['optimal_sharpe']} "
                f"(Current: {optimal['current_sharpe']})")
    logger.info("=" * 60)

    return {
        "var": var_results,
        "stress_summary": stress_summary,
        "risk_contrib": risk_contrib,
        "regime_stats": regime_stats,
        "tail_stats": tail_stats,
        "optimal": optimal,
        "mc_summary": mc_summary,
    }


if __name__ == "__main__":
    results = run_risk_analysis()

    print("\n===== VaR COMPARISON =====")
    print(results["var"].to_string(index=False))

    print("\n===== STRESS TEST SUMMARY =====")
    print(results["stress_summary"].to_string(index=False))

    print("\n===== RISK CONTRIBUTION =====")
    print(results["risk_contrib"][["ticker", "weight", "pct_of_total_risk",
                                    "risk_weight_ratio"]].to_string(index=False))

    print("\n===== OPTIMAL PORTFOLIO =====")
    for k, v in results["optimal"].items():
        print(f"  {k}: {v}")
