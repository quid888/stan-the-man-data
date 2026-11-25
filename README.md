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
| `data/macro/fred_*.csv` | US 10-year and 3-month yields, Fed funds, Swiss and German 10-year yields, Swiss 3-month rate, Swiss CPI | FRED | weekly |
| `data/macro/snb_rendoblim.csv` | Yields on Swiss Confederation bonds by maturity | SNB data portal | weekly |
| `data/derived/prices_chf.csv` | Adjusted prices of every ticker converted to CHF, from 2000 | `risk.py` | after each price run |
| `data/derived/risk_metrics.csv` | Per ticker, in CHF: 5-year return and volatility, maximum drawdown (all history and 5 years), 1-day and 1-month VaR and expected shortfall at 95%, beta and downside beta to the SPI, beta to MSCI World, correlation with gold, worst calendar year, 2008 and 2022 returns | `risk.py` | after each price run |
| `data/derived/correlation_weekly_5y.csv` | Weekly correlations of the holdings, candidate, gold, SPI and MSCI World | `risk.py` | after each price run |
| `data/derived/factor_loadings.csv` | Five-factor plus momentum regression, last 60 months, returns in USD to match the factors: alpha a year, loadings, t-stats, R-squared | `risk.py` | after each price run |
| `data/status.csv` | One line per item per run: ok or failed, rows, note. Check it when something looks stale | all scripts | each run |

## Jobs
- `daily-prices`: weekdays 22:17 UTC (after the US close): prices, then risk numbers. A Saturday run, or "Run workflow" with full = 1, re-downloads full history (Yahoo rewrites adjusted prices after every dividend).
- `weekly-fundamentals`: Saturdays 05:23 UTC: statements, estimates, dividends, factors, rates, then risk numbers.
- Both also run when code or the watchlist changes on `main`, and by hand from the Actions tab.

## Limits
- Yahoo Finance is unofficial and rate-limits cloud servers. The scripts go slowly (one ticker every few seconds, retries with back-off) and keep the last good file when a request fails; failures show in `data/status.csv`.
- Yahoo data is for personal research, not redistribution; keep this repo private.
- No Swiss insider-transaction feed yet (SIX publishes them on its website only).
- Kenneth French's international factors are in USD, which is why the factor regressions use USD returns.

## Use in a session
`git clone --depth 1 https://github.com/quid888/stan-the-man-data` (or `git pull --ff-only` in an existing clone), then read the CSVs with pandas. `python tests/offline_test.py` checks the parsers and the risk script without network access.
