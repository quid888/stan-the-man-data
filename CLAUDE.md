# Instructions for Claude sessions

This repo is the data store for the Stan the Man stock analysis. The method lives in the claude.ai project "Stan the Man": `claude/Stock-Pick Analysis Framework.md` (nine stages: outside view, guidance screen, moat, financial quality, management, valuation, risk and fit, decision with the Kahneman checks, monitoring).

## Using the data
- Pull first: `git -C <repo> pull --ff-only`, then check `data/status.csv` for failed or stale feeds before relying on a file.
- Tickers are Yahoo symbols; file names replace `^` and `=` with `_` (`^SSHI` -> `_SSHI.csv`, `GC=F` -> `GC_F.csv`). `config/watchlist.csv` gives each ticker's role, currency and price unit; London prices are in pence (`price_unit` = pence).
- Use `adj_close` for returns (dividends reinvested) and `close` for price levels. `data/derived/prices_chf.csv` already converts everything to CHF.
- Statements in `data/fundamentals/<ticker>/` have one row per period end and Yahoo's line-item names as columns; values are in the company's reporting currency (`snapshot.csv` column `financialCurrency`).
- Factor files are in percent and in USD; divide by 100. `factor_loadings.csv` is computed on USD returns for that reason.
- These numbers count as FACT only with their source and date: cite "Yahoo Finance via stan-the-man-data, <date>" (or Kenneth French, FRED, SNB). Cross-check key figures (EPS, net debt, dividends) against the company's own reports before they drive a decision; Yahoo's line items can differ from company definitions.
- For gold in CHF: `GC=F` close times `CHF=X` (CHF per USD).
- Swiss Confederation yields in `data/macro/snb_*.csv` end in July 2025 (SNB method change); for the current 10-year yield use the SNB website until the new series is added. US yields: `ust_yield_curve.csv`.

## Rules
- Never commit API keys or passwords; this pipeline needs none.
- Data files are written by the scripts; don't hand-edit them. Change `config/watchlist.csv` to add or remove tickers.
- Code changes go on a branch with a pull request; the data commits come from the bot on `main`.
