# stan-the-man-data

Data pipeline for the Stan the Man stock analysis (claude.ai project "Stan the Man", method in `claude/Stock-Pick Analysis Framework.md`). GitHub Actions pull free public data and commit it here, so analysis sessions can clone the repo and compute without a paid data feed.

## What's here
| Path | What | Source | Updated |
|---|---|---|---|
| `config/watchlist.csv` | Tickers followed: holdings, candidates, peers, benchmarks, gold, FX. Edit this file to add or remove names | by hand | |
| `data/prices/<ticker>.csv` | Daily open, high, low, close, adjusted close (dividends reinvested), volume, dividend, split. Full history | Yahoo Finance (yfinance) | weekdays |
| `data/dividends/<ticker>.csv` | Every dividend: ex-date, amount per share | Yahoo Finance | weekly |
| `data/fundamentals/<ticker>/*_annual.csv`, `*_quarterly.csv` | Income statement, balance sheet, cash flow; one row per period. Yahoo serves about four years, and the files keep every period ever seen, so history grows | Yahoo Finance | weekly |
| `data/fundamentals/<ticker>/snapshot.csv` | One row per week: market value, P/E, EV/EBITDA, yield, payout, beta, debt, cash, margins, analyst targets and count | Yahoo Finance | weekly |
| `data/fundamentals/<ticker>/estimates_*.csv`, `eps_trend.csv`, `growth.csv` | Analyst consensus for EPS and revenue, revisions, growth (missing for some European names) | Yahoo Finance | weekly |
| `data/factors/*.csv` | Fama-French five factors and momentum, Europe and Developed, monthly (and daily for Europe), in percent, in USD | Kenneth French data library | weekly |
| `data/macro/snb_rendoblim.csv` | Yields on Swiss Confederation bonds by maturity (1 to 30 years), monthly, 1988 to July 2025 (the SNB's old method) | SNB data portal | weekly |
| `data/macro/snb_rendoblid.csv` | Swiss Confederation bond yields by maturity, daily, to July 2025 (also the old method) | SNB data portal | weekly |
| `data/macro/ust_yield_curve.csv` | US Treasury par yield curve, daily since 2000, 1 month to 30 years | US Treasury | weekly |
| `data/macro/fred_*.csv` | Fed funds, Swiss 3-month rate, Swiss CPI and others, when FRED answers (it often does not answer GitHub's servers, so it gets 45 seconds) | FRED | weekly |
| `data/derived/prices_chf.csv` | Adjusted prices of every ticker converted to CHF, from 2000 | `risk.py` | after each price run |
| `data/derived/risk_metrics.csv` | Per ticker, in CHF: 5-year return and volatility, maximum drawdown (all history and 5 years), 1-day and 1-month VaR and expected shortfall at 95%, beta and downside beta to the SPI, beta to MSCI World, correlation with gold, worst calendar year, 2008 and 2022 returns | `risk.py` | after each price run |
| `data/derived/correlation_weekly_5y.csv` | Weekly correlations of the holdings, candidate, gold, SPI and MSCI World | `risk.py` | after each price run |
| `data/derived/factor_loadings.csv` | Five-factor plus momentum regression, last 60 months, returns in USD to match the factors: alpha a year, loadings, t-stats, R-squared | `risk.py` | after each price run |
| `data/macro/shiller_us.csv` | Robert Shiller's monthly US market data since 1871: S&P price, dividends, earnings, CPI, 10-year yield, CAPE, total-return CAPE, excess CAPE yield | shillerdata.com (ie_data.xls) | weekly |
| `data/derived/regime.csv` | Market regime from key indexes, one row per indicator: CAPE and excess CAPE yield (with the 10-year real return they imply), yield curve and the NY Fed recession probability, VIX and MOVE, high-yield against Treasury bonds, indexes against their 200-day average, equal against cap weight, small against large, value against growth, EUR/CHF, copper/gold. Value, percentile, reading, derived result, points toward fragility | `regime.py` | after each run |
| `data/derived/regime_summary.csv` | One row: valuation state, fragility score, regime (Green light, Burry zone, Normal, Caution, Munger zone, Late cycle, Defense), what to do, hurdle rate for a single stock (Munger's opportunity cost), required margin of safety for new buys | `regime.py` | after each run |
| `data/derived/regime_history.csv` | Monthly since 1990: CAPE, excess CAPE yield, the model's 10-year forecast and the realised 10-year return, yield curve, recession probability, VIX, credit, concentration ratios | `regime.py` | after each run |
| `data/derived/scores.csv` | For every holding, candidate and peer: Munger quality, Burry value, forensic safety (0 to 100), ick (how unloved), reverse discounted cash flow (growth the price implies against a conservative estimate), margin of safety, expected return against the hurdle, data flags, and a label | `scores.py` | after each run |
| `data/derived/scores_detail.csv` | Every metric behind the scores with its value, points out of 10 and weight | `scores.py` | after each run |
| `data/status.csv` | One line per item per run: ok or failed, rows, note. Check it when something looks stale | all scripts | each run |

## Jobs
- `daily-prices`: weekdays 22:17 UTC (after the US close): prices, then risk numbers, market regime and scores. A Saturday run, or "Run workflow" with full = 1, re-downloads full history (Yahoo rewrites adjusted prices after every dividend).
- `weekly-fundamentals`: Saturdays 05:23 UTC: statements, estimates, dividends, factors, rates, risk numbers, then the market regime (with a fresh copy of Shiller's data) and the scores.
- Both also run when code or the watchlist changes on `main`, and by hand from the Actions tab.

## Limits
- Yahoo Finance is unofficial and rate-limits cloud servers. The scripts go slowly (one ticker every few seconds, retries with back-off) and keep the last good file when a request fails; failures show in `data/status.csv`.
- Yahoo data is for personal research, not redistribution; keep this repo private.
- No Swiss insider-transaction feed yet (SIX publishes them on its website only).
- Kenneth French's international factors are in USD, which is why the factor regressions use USD returns.
- Swiss bond yields after July 2025: the SNB moved to a new method and its new data-portal series is not wired in yet. Until it is, take the current 10-year Confederation yield from the SNB's "current interest rates" page.
- FRED does not answer GitHub's servers; US yields come from the US Treasury instead.

## Burry and Munger layers: how the scores are built
- Scales and weights are at the top of `scripts/scores.py` (`SCALES`, `WEIGHTS`); each metric earns 0 to 10 points on a piecewise-linear scale, and a score needs at least half its weight in data.
- Munger quality: return on invested capital (average and worst year), gross margin (level and stability), free cash flow against net income, capex against sales, net debt against EBITDA, change in share count.
- Burry value: free-cash-flow yield after stock pay, lease payments and minority interests; EV/EBITDA; EV/(EBITDA - capex); growth cushion (a conservative growth estimate minus the growth the price implies over ten years at the required return); tangible book against price; net buybacks.
- Forensic safety: Beneish M-score, Sloan accruals, Piotroski F-score, Altman Z'' (non-manufacturing), receivables against sales, capex against depreciation, stock pay against free cash flow. Not meant for banks or insurers.
- Data checks: Yahoo's statement debt can miss bonds (Swisscom's 2025 statement shows only leases), so the larger of the statement and snapshot debt is used and a gap is flagged in `data_flags`. Read the flags before trusting a score, and cross-check the inputs that drive a decision against the company's report.
- The market regime sets two inputs for the scores: the hurdle rate (expected 10-year real return of US stocks, at least 0, plus 3 points for concentration, plus 1% Swiss inflation) and the required margin of safety (15% to 35% by regime). The required return in the reverse cash-flow test is the hurdle with a 7% floor.
- `python tests/model_test.py` checks the NY Fed probit against the NY Fed's published values, the Beneish formula, the scales and the Shiller parser.

## Use in a session
`git clone --depth 1 https://github.com/quid888/stan-the-man-data` (or `git pull --ff-only` in an existing clone), then read the CSVs with pandas. `python tests/offline_test.py` checks the parsers and the risk script without network access.
