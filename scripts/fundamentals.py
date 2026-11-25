"""Weekly company fundamentals and analyst consensus from Yahoo Finance (yfinance).

Per company (holdings, candidates and peers):
data/fundamentals/<ticker>/
  income_annual.csv, balance_annual.csv, cashflow_annual.csv   one row per fiscal year
  income_quarterly.csv, balance_quarterly.csv, cashflow_quarterly.csv
  snapshot.csv          one row per weekly run: market cap, P/E, yield, beta, debt, cash, targets ...
  estimates_eps.csv, estimates_revenue.csv, eps_trend.csv, growth.csv   consensus, overwritten each run
data/dividends/<ticker>.csv   every dividend paid (ex-date, amount per share)

Yahoo only serves about four years of statements, so each run merges new periods into the
file and never drops older ones: history builds up over time.
"""
import os
import sys
import time

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
from common import DATA, log_status, now_utc, retry, safe_name, watchlist, write_csv  # noqa: E402

STATEMENTS = {
    "income_annual": "income_stmt", "balance_annual": "balance_sheet", "cashflow_annual": "cashflow",
    "income_quarterly": "quarterly_income_stmt", "balance_quarterly": "quarterly_balance_sheet",
    "cashflow_quarterly": "quarterly_cashflow",
}
SNAPSHOT_FIELDS = [
    "currency", "financialCurrency", "marketCap", "enterpriseValue", "sharesOutstanding", "floatShares",
    "trailingPE", "forwardPE", "priceToBook", "enterpriseToEbitda", "enterpriseToRevenue",
    "dividendRate", "dividendYield", "payoutRatio", "exDividendDate", "beta", "totalDebt", "totalCash",
    "ebitda", "freeCashflow", "operatingCashflow", "revenueGrowth", "earningsGrowth", "grossMargins",
    "operatingMargins", "profitMargins", "returnOnEquity", "returnOnAssets", "heldPercentInsiders",
    "heldPercentInstitutions", "targetMeanPrice", "targetLowPrice", "targetHighPrice",
    "numberOfAnalystOpinions", "recommendationMean", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "regularMarketPrice",
]
ESTIMATES = {"estimates_eps": "earnings_estimate", "estimates_revenue": "revenue_estimate",
             "eps_trend": "eps_trend", "growth": "growth_estimates"}


def merge_periods(new: pd.DataFrame, path):
    """new: yfinance statement (items x period columns). Store periods as rows, keep old periods."""
    rows = new.T
    rows.index = pd.to_datetime(rows.index).normalize()
    rows.index.name = "period_end"
    if path.exists():
        old = pd.read_csv(path, index_col="period_end", parse_dates=True)
        rows = pd.concat([old[~old.index.isin(rows.index)], rows]).sort_index()
    write_csv(rows, path)
    return len(rows)


def run(ticker):
    t = yf.Ticker(ticker)
    folder = DATA / "fundamentals" / safe_name(ticker)
    done = []
    for name, attr in STATEMENTS.items():
        try:
            df = retry(lambda: getattr(t, attr), tries=3, what=f"{ticker} {attr}")
            if df is not None and not df.empty:
                done.append(f"{name}:{merge_periods(df, folder / f'{name}.csv')}")
        except Exception as e:  # noqa: BLE001
            log_status("fundamentals", f"{ticker} {name}", False, 0, e)
        time.sleep(1)
    try:
        info = retry(lambda: t.info, tries=3, what=f"{ticker} info") or {}
        snap = {"run_utc": now_utc(), **{k: info.get(k) for k in SNAPSHOT_FIELDS}}
        path = folder / "snapshot.csv"
        old = pd.read_csv(path) if path.exists() else pd.DataFrame()
        write_csv(pd.concat([old, pd.DataFrame([snap])], ignore_index=True), path, index=False)
        done.append("snapshot")
    except Exception as e:  # noqa: BLE001
        log_status("fundamentals", f"{ticker} snapshot", False, 0, e)
    for name, attr in ESTIMATES.items():
        try:
            df = getattr(t, attr)
            if df is not None and not df.empty:
                df = df.copy()
                df.insert(0, "run_utc", now_utc())
                write_csv(df, folder / f"{name}.csv")
                done.append(name)
        except Exception as e:  # noqa: BLE001 (estimates are missing for many non-US names)
            log_status("fundamentals", f"{ticker} {name}", False, 0, e)
    try:
        d = t.dividends
        if d is not None and len(d):
            d = d.copy()
            d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
            write_csv(d.rename("dividend").rename_axis("ex_date").to_frame(), DATA / "dividends" / f"{safe_name(ticker)}.csv")
            done.append(f"dividends:{len(d)}")
    except Exception as e:  # noqa: BLE001
        log_status("fundamentals", f"{ticker} dividends", False, 0, e)
    return done


def main():
    wl = watchlist()
    names = wl[wl["role"].isin(["holding", "candidate", "peer"])]["ticker"]
    for tk in names:
        done = run(tk)
        log_status("fundamentals", tk, bool(done), len(done), ", ".join(done))
        print(tk, done)
        time.sleep(3)


if __name__ == "__main__":
    main()
