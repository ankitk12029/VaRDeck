# VaRDeck Data Dictionary

Every table in `portfolio_dashboard.db`, grouped by source module. 22 tables total:
6 from the core ETL pipeline (`etl_pipeline.py`) and 16 from the risk analysis
engine (`risk_analysis.py`), one of which (`optimal_weights`) was split out of
`optimal_portfolio` to store per-ticker weights in a BI-friendly long format
(SQLite can't store a nested dict in a single cell).

---

## Core Pipeline Tables (`etl_pipeline.py`)

### `risk_metrics`
One row per ticker. Point-in-time annualized risk/return stats.

| Column | Type | Definition |
|---|---|---|
| `ticker` | TEXT | Stock/ETF symbol |
| `sector` | TEXT | Sector label from the `TICKERS` config |
| `weight` | FLOAT | Target portfolio weight (0 for benchmark-only tickers) |
| `annualized_return` | FLOAT | Mean daily return × 252 |
| `annualized_volatility` | FLOAT | Daily return std × √252 |
| `sharpe_ratio` | FLOAT | (Annualized return − risk-free rate) / annualized volatility |
| `sortino_ratio` | FLOAT | Same as Sharpe but denominator uses only downside deviation (std of negative-return days × √252) |
| `max_drawdown` | FLOAT | Largest peak-to-trough decline over the lookback window |
| `var_95` | FLOAT | 5th percentile of daily returns (1-day, 95% VaR, ticker-level) |
| `cvar_95` | FLOAT | Mean of returns at/below `var_95` (Expected Shortfall) |
| `beta` | FLOAT | Cov(ticker, benchmark) / Var(benchmark); benchmark's own beta is fixed at 1.0 |

### `portfolio_returns`
One row per trading day. Weighted portfolio performance vs. benchmark.

| Column | Type | Definition |
|---|---|---|
| `date` | DATETIME | Trading day |
| `portfolio_return` | FLOAT | Weighted sum of held tickers' daily returns |
| `cumulative_return` | FLOAT | Compounded portfolio return since the start of the lookback window |
| `benchmark_return` | FLOAT | SPY daily return |
| `benchmark_cumulative` | FLOAT | Compounded SPY return since the start of the lookback window |

### `correlation_matrix`
Rolling 90-day pairwise correlation between held tickers, sampled weekly (Fridays only) to keep row count manageable. Long format, upper triangle only (`ticker_a` ≤ `ticker_b` by iteration order).

| Column | Type | Definition |
|---|---|---|
| `date` | DATETIME | Friday snapshot date |
| `ticker_a` | TEXT | First ticker in the pair |
| `ticker_b` | TEXT | Second ticker in the pair |
| `correlation` | FLOAT | 90-day rolling Pearson correlation of daily returns |

### `drawdown_series`
Daily drawdown-from-peak, one row per ticker per day (long format).

| Column | Type | Definition |
|---|---|---|
| `date` | DATETIME | Trading day |
| `ticker` | TEXT | Symbol |
| `drawdown` | FLOAT | (Price − rolling all-time-high price) / rolling all-time-high price. Always ≤ 0. |

### `daily_prices`
Raw adjusted close prices, long format, one row per ticker per day.

| Column | Type | Definition |
|---|---|---|
| `date` | DATETIME | Trading day |
| `ticker` | TEXT | Symbol |
| `close_price` | FLOAT | Adjusted close (splits/dividends applied via `auto_adjust=True`) |

### `refresh_log`
Append-only. One row per pipeline run, for freshness/audit tracking.

| Column | Type | Definition |
|---|---|---|
| `run_timestamp` | DATETIME | When the pipeline ran |
| `tickers_count` | BIGINT | Number of tickers configured |
| `data_start` / `data_end` | DATETIME | Date range of the price data pulled |
| `trading_days` | BIGINT | Row count of the price history |

---

## Risk Analysis Engine Tables (`risk_analysis.py`)

### `var_analysis` — Module 1
9 rows: 3 VaR methods × 3 confidence levels.

| Column | Type | Definition |
|---|---|---|
| `method` | TEXT | "Historical Simulation", "Parametric (Normal)", or "Monte Carlo" |
| `confidence_level` | FLOAT | 0.90, 0.95, or 0.99 |
| `var_pct` | FLOAT | Value at Risk as a fraction of portfolio value |
| `var_dollar` | FLOAT | VaR in dollars, assuming `portfolio_value` |
| `cvar_pct` / `cvar_dollar` | FLOAT | Conditional VaR (Expected Shortfall) — mean loss in the tail beyond VaR |
| `portfolio_value` | BIGINT | Notional portfolio value used for dollar conversion (default $100,000) |
| `calculation_date` | DATE | Date the VaR was computed |

### `stress_test_detail` / `stress_test_summary` — Module 2
Replays 6 historical crises against current weights. `_detail` is per-ticker;
`_summary` is portfolio-level (one row per scenario, `ticker` = "PORTFOLIO").
Note both share the same underlying columns (`_summary` was built via a
`groupby` + a synthetic `PORTFOLIO` row appended from `_detail`).

| Column | Type | Definition |
|---|---|---|
| `scenario` | TEXT | Crisis name (e.g. "2008 Financial Crisis", "COVID-19 Crash") |
| `period_start` / `period_end` | TEXT | Date range of the crisis window |
| `trading_days` | BIGINT | Number of trading days in that window |
| `ticker` | TEXT | Symbol, or "PORTFOLIO" in the summary rows |
| `ticker_return` | FLOAT | Total return over the crisis window for that ticker |
| `weight` | FLOAT | Portfolio weight applied |
| `weighted_impact` | FLOAT | `ticker_return × weight` |
| `portfolio_impact` (summary only) | FLOAT | Sum of weighted impacts = total portfolio loss/gain in that crisis |
| `worst_ticker_return` / `best_ticker_return` (summary only) | FLOAT | Range of individual ticker outcomes within the scenario |

### `scenario_analysis` — Module 3
54 rows: 6 hypothetical shocks × (8 held tickers + 1 portfolio total row).

| Column | Type | Definition |
|---|---|---|
| `scenario` | TEXT | Hypothetical scenario name (e.g. "Tech Bubble Burst") |
| `scenario_type` | TEXT | Always "Hypothetical" (distinguishes from historical stress tests) |
| `ticker` | TEXT | Symbol, or "PORTFOLIO" for the total row |
| `sector` | TEXT | Sector (or "ALL" for the portfolio row) |
| `weight` | FLOAT | Portfolio weight |
| `sector_shock` | FLOAT | Assumed sector-level return shock for this scenario |
| `weighted_impact` | FLOAT | `sector_shock × weight` |
| `dollar_impact` | FLOAT | `weighted_impact × portfolio_value` (default $100,000) |

### `risk_decomposition` — Module 4
One row per held ticker. Answers "who's really driving portfolio risk."

| Column | Type | Definition |
|---|---|---|
| `ticker` | TEXT | Symbol |
| `weight` | FLOAT | Portfolio weight |
| `standalone_vol` | FLOAT | Ticker's own annualized volatility |
| `marginal_contribution` | FLOAT | ∂(portfolio vol)/∂(weight) — how much portfolio vol changes per unit of added weight |
| `component_risk` | FLOAT | `weight × marginal_contribution`; sums across all tickers to total portfolio volatility |
| `pct_of_total_risk` | FLOAT | `component_risk / portfolio_volatility` |
| `risk_weight_ratio` | FLOAT | `pct_of_total_risk / weight` — >1 means the position contributes disproportionately more risk than its weight |
| `portfolio_volatility` | FLOAT | Total annualized portfolio volatility (same value repeated on every row) |

### `risk_regimes_daily` / `risk_regime_stats` — Module 5
`_daily` classifies each trading day; `_stats` summarizes performance by regime.

| Column | Type | Definition |
|---|---|---|
| `date` | DATETIME | Trading day (daily table only) |
| `rolling_vol` | FLOAT | 21-day rolling annualized volatility (daily table only) |
| `regime` | TEXT | "Low" / "Normal" / "Elevated" / "High" / "Crisis" — see [risk_methodology.md](risk_methodology.md) for thresholds |
| `days_count` (stats only) | BIGINT | Number of days classified into this regime |
| `pct_of_time` (stats only) | FLOAT | Share of trading days in this regime |
| `avg_daily_return` / `annualized_return` (stats only) | FLOAT | Return performance conditional on being in this regime |
| `avg_volatility` (stats only) | FLOAT | Average rolling vol while in this regime |
| `sharpe_in_regime` (stats only) | FLOAT | Sharpe ratio conditional on this regime |

### `tail_risk_stats` — Module 6
1 row. Distributional shape of portfolio daily returns.

| Column | Type | Definition |
|---|---|---|
| `mean_daily` / `std_daily` | FLOAT | Daily return mean and standard deviation |
| `skewness` | FLOAT | Third moment — asymmetry of the return distribution |
| `kurtosis` | FLOAT | Excess kurtosis (0 = normal distribution; positive = fatter tails than normal) |
| `jarque_bera_stat` / `jarque_bera_pvalue` | FLOAT | Jarque-Bera normality test statistic and p-value |
| `is_normal` | TEXT | "No" if p-value < 0.05 (rejects normality), else "Yes" |
| `worst_day` / `best_day` | FLOAT | Single best/worst daily return in the sample |
| `pct_negative_days` | FLOAT | Share of days with a negative return |
| `pct_beyond_2sigma` / `pct_beyond_3sigma` | FLOAT | Observed frequency of returns beyond 2/3 standard deviations |
| `expected_2sigma_pct` / `expected_3sigma_pct` | FLOAT | Theoretical frequency under a normal distribution (4.55% and 0.27%) — compare against the observed columns above |

### `extreme_days` — Module 6
40 rows: top 20 worst + top 20 best single days.

| Column | Type | Definition |
|---|---|---|
| `date` | DATETIME | Trading day |
| `return` | FLOAT | Portfolio return that day |
| `rank` | BIGINT | 1 = most extreme within its `type` |
| `type` | TEXT | "Worst" or "Best" |

### `return_distribution` — Module 6
50 rows. Histogram of portfolio daily returns vs. a fitted normal curve, for overlay charts.

| Column | Type | Definition |
|---|---|---|
| `bin_center` | FLOAT | Midpoint of the histogram bin |
| `actual_count` | BIGINT | Observed number of days in this bin |
| `normal_expected` | FLOAT | Expected count if returns were normally distributed with the same mean/std |
| `excess` | FLOAT | `actual_count − normal_expected`; positive spikes indicate fat tails |

### `efficient_frontier` — Module 7
~60 rows: frontier curve points + the current portfolio + one point per individual asset.

| Column | Type | Definition |
|---|---|---|
| `target_return` | FLOAT | Annualized return at this point |
| `min_volatility` | FLOAT | Minimum achievable volatility for that target return (or the asset's/portfolio's actual vol) |
| `sharpe` | FLOAT | Sharpe ratio at this point |
| `point_type` | TEXT | "frontier", "current_portfolio", or "asset_{TICKER}" |

### `optimal_portfolio` / `optimal_weights` — Module 7
`optimal_portfolio` is the 1-row summary (max-Sharpe portfolio found via SLSQP
optimization, weights capped at 40% per position). `optimal_weights` holds the
per-ticker breakdown of that optimal allocation in long format — split into
its own table because SQLite cannot store the nested dict the optimizer
returns natively in one column.

| Column | Type | Definition |
|---|---|---|
| `optimal_return` / `optimal_volatility` / `optimal_sharpe` | FLOAT | Performance of the max-Sharpe portfolio found by the optimizer |
| `current_return` / `current_volatility` / `current_sharpe` | FLOAT | Performance of the actual configured weights, for comparison |
| `ticker` (optimal_weights) | TEXT | Symbol |
| `weight` (optimal_weights) | FLOAT | Optimizer's suggested weight for that ticker (sums to 1.0 across all rows) |

### `monte_carlo_paths` / `monte_carlo_summary` — Module 8
`_paths` is 253 rows (day 0 through day 252) of percentile bands across 5,000
simulated portfolio paths. `_summary` is 1 row of terminal-value statistics.

| Column | Type | Definition |
|---|---|---|
| `day` | BIGINT | Simulation day (0 = today, 252 = 1 year forward) |
| `p5`...`p95` | FLOAT | Percentile of simulated portfolio value across all paths on that day |
| `mean` | FLOAT | Mean simulated portfolio value on that day |
| `median_final` / `mean_final` (summary) | FLOAT | Terminal (day-252) value statistics |
| `p5_final` / `p95_final` (summary) | FLOAT | 90% confidence interval on terminal value |
| `prob_loss` (summary) | FLOAT | Probability the terminal value is below the starting value |
| `prob_gain_10pct` / `prob_loss_10pct` (summary) | FLOAT | Probability of a >10% gain / >10% loss over the horizon |
| `expected_shortfall_5pct` (summary) | FLOAT | Mean terminal value across the worst 5% of simulated paths |

### `risk_analysis_log` — metadata
Append-only, mirrors `refresh_log` for the risk engine specifically.

| Column | Type | Definition |
|---|---|---|
| `run_timestamp` | DATETIME | When the risk engine ran |
| `diversification_ratio` | FLOAT | Weighted-average standalone vol ÷ portfolio vol (>1 = diversification is reducing risk) |
| `modules_run` | BIGINT | Always 8 |
| `tickers_analyzed` | BIGINT | Number of held (non-zero-weight) tickers |
