# Risk Methodology

Documents the assumptions behind every module in `risk_analysis.py` so the
"why" can be defended in an interview, not just the "what."

Portfolio configuration used throughout: 8 held positions (AAPL 20%, MSFT 15%,
JPM 15%, JNJ 10%, XOM 10%, PG 10%, BND 10%, GLD 10%) benchmarked against SPY
(0% weight — tracked for comparison only). Risk-free rate assumed at 5%
annual, held constant. Lookback window is 3 years of daily adjusted-close
prices from Yahoo Finance.

---

## Module 1 — Value at Risk (3 methods)

**Why three methods instead of one:** each makes a different tradeoff, and
the size of the disagreement between them is itself a risk signal.

- **Historical Simulation** — takes the empirical 5th/1st percentile of
  actual portfolio daily returns. No distributional assumption, so it
  captures real fat tails and skew, but it's only as good as the historical
  window; it assumes the past 3 years are representative of future risk.
- **Parametric (Variance-Covariance)** — assumes portfolio returns are
  normally distributed, computes VaR from the mean/std analytically via the
  z-score at each confidence level. Fast and simple, but this codebase's own
  `tail_risk_stats` table shows the portfolio's returns fail the Jarque-Bera
  normality test (fat tails, positive excess kurtosis) — so parametric VaR is
  expected to **understate** true tail risk here.
- **Monte Carlo** — simulates 10,000 correlated return draws using a Cholesky
  decomposition of the historical covariance matrix (falling back to the
  nearest positive-definite matrix if the empirical covariance isn't
  invertible). Handles cross-asset correlation explicitly and doesn't require
  a closed-form distribution, at the cost of being sensitive to the
  covariance estimate itself.

All three are computed at 90%, 95%, and 99% confidence, both as a % of
portfolio value and in dollars against a $100,000 notional. CVaR (Expected
Shortfall) is reported alongside VaR at each level — the mean loss *given*
that the VaR threshold was breached, which by construction is always ≥ VaR.

## Module 2 — Historical Stress Testing

**Why these six windows:** each was chosen to stress a different
correlation/liquidity regime, not just "biggest drawdowns."

| Scenario | Window | What it stresses |
|---|---|---|
| 2008 Financial Crisis | 2008-09-01 → 2009-03-09 | Credit crisis, correlation breakdown across every asset class including normally-uncorrelated ones |
| 2011 EU Debt Crisis | 2011-07-01 → 2011-10-03 | Sovereign risk contagion, non-US shock transmitted to US equities |
| 2015 China Slowdown | 2015-08-10 → 2015-08-25 | Fast, short-duration volatility spike (flash-crash-like) |
| 2018 Q4 Selloff | 2018-10-01 → 2018-12-24 | Rate-driven selloff with no external "crisis" trigger |
| COVID-19 Crash | 2020-02-19 → 2020-03-23 | Fastest drawdown in modern history; tests liquidity/gap risk |
| 2022 Rate Hike Selloff | 2022-01-03 → 2022-10-12 | Slow-grind bear market driven by monetary policy, not a shock event |

Each scenario replays actual ticker returns over that exact historical window
(a fresh `yfinance` pull, since 3 years of lookback doesn't cover 2008-2015)
and applies **current** portfolio weights — this is a "what if this happened
to today's portfolio" test, not a backtest of what the portfolio actually
earned at the time. Tickers with incomplete data for a window (e.g., an ETF
that didn't exist yet) are silently excluded from that scenario and logged as
a warning; this can slightly understate a scenario's severity if a
now-material holding lacks history.

## Module 3 — Hypothetical Scenario Analysis

Unlike Module 2, these are forward-looking, made-up shocks with no
historical precedent required — useful for testing exposures that haven't
happened yet (e.g., a rate spike beyond anything in the 3-year lookback).
Shocks are applied at the **sector** level (Technology, Financials,
Healthcare, Energy, Consumer, Fixed Income, Commodities) and then weighted by
each ticker's actual portfolio weight. The shock magnitudes themselves
(-15% to +35% depending on scenario/sector) are judgment calls calibrated to
be "plausible tail event," not derived from any model — this is the
honest caveat to give in an interview: hypothetical scenarios are only as
good as the analyst's assumptions about magnitude.

## Module 4 — Risk Contribution & Decomposition

Answers "is a ticker's *weight* the same as its *risk contribution*?"
Almost never, because volatility and correlation compound. The math:

- Portfolio variance: `σ²_p = wᵀ Σ w` (Σ = annualized covariance matrix)
- Marginal Contribution to Risk: `MCR_i = (Σw)_i / σ_p` — the partial
  derivative of portfolio volatility with respect to ticker `i`'s weight
- Component risk: `weight_i × MCR_i` — these sum exactly to `σ_p` across all
  tickers (verified in `tests/test_risk_analysis.py`)
- `risk_weight_ratio = pct_of_total_risk / weight` — the headline number.
  A ratio > 1 means a ticker contributes more portfolio risk than its capital
  weight would suggest (high vol and/or high correlation with the rest of the
  book); < 1 means it's a diversifier.

**Diversification Ratio** = weighted-average standalone volatility ÷ actual
portfolio volatility. Always ≥ 1 by construction (unless every pairwise
correlation is exactly 1); higher means diversification is doing more work.

## Module 5 — Rolling Risk Regime Detection

21-day rolling annualized volatility of portfolio returns is bucketed into
five regimes:

| Regime | Annualized Vol Range |
|---|---|
| Low | 0% – 10% |
| Normal | 10% – 18% |
| Elevated | 18% – 25% |
| High | 25% – 35% |
| Crisis | 35%+ |

**Why these cutoffs:** they're anchored to VIX-equivalent intuition — SPY's
long-run annualized volatility sits around 15-18%, so "Normal" is centered
there; "Crisis" (35%+) roughly corresponds to VIX readings seen in 2008 and
March 2020. These are judgment-call thresholds, not statistically fit
breakpoints (e.g., not from a Markov-switching model) — worth stating plainly
if asked, since a more rigorous approach would fit regime boundaries
statistically (e.g., Hidden Markov Model on realized vol) rather than using
fixed bands. The 21-day window approximates one trading month.

Regime-conditional Sharpe ratios reveal whether a strategy's edge holds up
in stress: a portfolio with a strong unconditional Sharpe that collapses in
"Elevated"/"Crisis" regimes is more fragile than the headline number implies.

## Module 6 — Tail Risk Analysis

Reports skewness, excess kurtosis, and the Jarque-Bera test for normality on
the portfolio's daily return series. Also compares observed frequency of
returns beyond 2σ/3σ against the normal-distribution baseline (4.55% and
0.27% respectively) — the gap between observed and expected is direct
empirical evidence for or against using parametric (normal-assumption) risk
models. This module is what justifies (or disproves) the Module 1 parametric
VaR caveat with actual numbers instead of a general disclaimer.

## Module 7 — Efficient Frontier & Optimal Portfolio

Uses `scipy.optimize.minimize` (SLSQP) to find the maximum-Sharpe-ratio
portfolio subject to: weights sum to 1, no shorting (weight ≥ 0), and a 40%
position cap per ticker (prevents the optimizer from concentrating entirely
into the single highest-Sharpe historical asset, which is a well-known
failure mode of naive mean-variance optimization). The frontier curve itself
is generated by minimizing volatility at 50 fixed target-return levels.

**Important caveat to state out loud in an interview:** this is a
backward-looking optimization over realized 3-year returns and covariances.
It answers "what would have been optimal," not "what will be optimal" —
historical mean returns are especially noisy estimators of expected future
returns (much noisier than the covariance matrix), so treating the "optimal"
weights as an action plan rather than a diagnostic is a common and serious
mistake.

## Module 8 — Monte Carlo Simulation (full paths)

Simulates 5,000 independent 252-trading-day (1-year) paths using i.i.d.
draws from `Normal(portfolio daily mean, portfolio daily std)` compounded
day-over-day — a simple Geometric Brownian Motion-style walk at the
**portfolio** level (not simulating each ticker + correlation structure the
way Module 1's Monte Carlo VaR does). This is a simplification worth
disclosing: it assumes the portfolio's own historical mean/std fully
characterizes its future risk, ignoring the fat tails and regime-switching
behavior documented in Modules 5 and 6. It reports percentile bands (p5
through p95) at every day for fan-chart visualization, plus terminal-value
probabilities (loss, >10% gain, >10% loss, expected shortfall in the worst
5% of paths).

---

## Known Simplifications (for the "what would you do differently" question)

- Regime thresholds (Module 5) are fixed judgment-call bands, not
  statistically fit (see Module 5 above).
- Hypothetical scenario shock sizes (Module 3) are analyst assumptions, not
  model outputs.
- The Module 8 Monte Carlo simulates the portfolio as a single lognormal-ish
  process rather than simulating each asset with its correlation structure
  and re-weighting daily.
- Efficient frontier optimization (Module 7) uses historical returns as a
  proxy for expected returns, which is standard but weak practice — a more
  robust approach would use a factor model or shrinkage estimator (e.g.,
  Black-Litterman) for expected returns while keeping the empirical
  covariance matrix.
- All risk-free rate assumptions are a flat 5% annual; a real desk would use
  the actual term-matched Treasury yield at each historical point in time.
