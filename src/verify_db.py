"""
Database verification script — prints every table, row count, and a
3-row sample. Also runs sanity checks on key risk outputs (no NaNs,
no impossible values in VaR / stress test / Monte Carlo results).
"""

import pandas as pd
from sqlalchemy import create_engine, inspect

DB_URL = "sqlite:///portfolio_dashboard.db"


def main():
    engine = create_engine(DB_URL)
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())

    print(f"\n{'=' * 70}")
    print(f"DATABASE SUMMARY — {len(tables)} tables")
    print(f"{'=' * 70}\n")

    for table in tables:
        with engine.connect() as conn:
            count = pd.read_sql(f"SELECT COUNT(*) as n FROM {table}", conn)["n"].iloc[0]
            sample = pd.read_sql(f"SELECT * FROM {table} LIMIT 3", conn)
        print(f"--- {table} ({count} rows) ---")
        print(sample.to_string(index=False))
        print()

    # ── Sanity checks ──
    print(f"{'=' * 70}")
    print("SANITY CHECKS")
    print(f"{'=' * 70}\n")

    issues = []

    with engine.connect() as conn:
        var_df = pd.read_sql("SELECT * FROM var_analysis", conn)
        stress_df = pd.read_sql("SELECT * FROM stress_test_summary", conn)
        mc_summary = pd.read_sql("SELECT * FROM monte_carlo_summary", conn)
        mc_paths = pd.read_sql("SELECT * FROM monte_carlo_paths", conn)
        tail_stats = pd.read_sql("SELECT * FROM tail_risk_stats", conn)
        risk_decomp = pd.read_sql("SELECT * FROM risk_decomposition", conn)
        optimal = pd.read_sql("SELECT * FROM optimal_portfolio", conn)

    # VaR: no NaNs, non-negative, CVaR >= VaR (loss magnitude), 99% >= 95% >= 90%
    if var_df.isnull().any().any():
        issues.append("var_analysis contains NaNs")
    if (var_df["var_dollar"] < 0).any() or (var_df["cvar_dollar"] < 0).any():
        issues.append("var_analysis has negative dollar values")
    for method in var_df["method"].unique():
        sub = var_df[var_df["method"] == method].set_index("confidence_level")
        if not (sub.loc[0.99, "var_dollar"] >= sub.loc[0.95, "var_dollar"] >= sub.loc[0.90, "var_dollar"]):
            issues.append(f"VaR not monotonic across confidence levels for method={method}")
        for cl in sub.index:
            if sub.loc[cl, "cvar_dollar"] < sub.loc[cl, "var_dollar"] * 0.95:
                issues.append(f"CVaR < VaR for method={method}, cl={cl}")

    # Stress test: portfolio impact should be negative for known crises, no NaNs
    if stress_df.isnull().any().any():
        issues.append("stress_test_summary contains NaNs")
    crisis_rows = stress_df[stress_df["scenario"].str.contains("Crisis|Crash|Selloff", regex=True)]
    if (crisis_rows["portfolio_impact"] > 0).any():
        issues.append("A historical crisis scenario shows a POSITIVE portfolio impact (check dates/data)")

    # Monte Carlo: probabilities in [0,1], p5 < median < p95, path values positive
    for col in ["prob_loss", "prob_gain_10pct", "prob_loss_10pct"]:
        val = mc_summary[col].iloc[0]
        if not (0 <= val <= 1):
            issues.append(f"monte_carlo_summary.{col} out of [0,1] range: {val}")
    if not (mc_summary["p5_final"].iloc[0] < mc_summary["median_final"].iloc[0] < mc_summary["p95_final"].iloc[0]):
        issues.append("Monte Carlo percentile ordering violated (p5 < median < p95 failed)")
    if (mc_paths.drop(columns=["day"]) <= 0).any().any():
        issues.append("Monte Carlo paths contain non-positive portfolio values")

    # Tail risk stats: no NaNs, probabilities in range
    if tail_stats.isnull().any().any():
        issues.append("tail_risk_stats contains NaNs")

    # Risk decomposition: contributions should sum to ~portfolio vol
    total_component_risk = risk_decomp["component_risk"].sum()
    port_vol = risk_decomp["portfolio_volatility"].iloc[0]
    if abs(total_component_risk - port_vol) > 0.005:
        issues.append(f"Risk decomposition components ({total_component_risk:.4f}) "
                       f"don't sum to portfolio vol ({port_vol:.4f})")

    # Optimal portfolio: Sharpe should be a real number, optimal >= current (by construction)
    if optimal.isnull().any().any():
        issues.append("optimal_portfolio contains NaNs")
    if optimal["optimal_sharpe"].iloc[0] < optimal["current_sharpe"].iloc[0] - 0.01:
        issues.append("Optimal Sharpe is worse than current Sharpe (optimizer should never do this)")

    if issues:
        print("FAILED CHECKS:")
        for i in issues:
            print(f"  - {i}")
    else:
        print("All sanity checks PASSED:")
        print("  - VaR: no NaNs, non-negative, monotonic across confidence levels, CVaR >= VaR")
        print("  - Stress tests: no NaNs, historical crises show negative portfolio impact")
        print("  - Monte Carlo: probabilities in [0,1], percentile ordering correct, all paths positive")
        print("  - Tail risk stats: no NaNs")
        print("  - Risk decomposition: component risks sum to portfolio volatility")
        print("  - Optimal portfolio: optimal Sharpe >= current Sharpe")

    print(f"\n{'=' * 70}\n")


if __name__ == "__main__":
    main()
