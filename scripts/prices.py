"""Daily prices for every ticker in config/watchlist.csv (Yahoo Finance via yfinance).

data/prices/<ticker>.csv: date, open, high, low, close, adj_close, volume, dividend, split
- First run downloads the full history; later runs fetch the last 15 days and merge.
- Yahoo's adj_close is rewritten backwards after every dividend, so the full history is
  re-downloaded when the recent window holds a dividend or split, on Saturdays, or with FULL=1.
- Failures keep the last good file and are logged in data/status.csv.
"""
import datetime as dt
import os
import sys
import time

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
from common import DATA, log_status, retry, safe_name, trim_status, watchlist, write_csv  # noqa: E402

COLS = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Adj Close": "adj_close",
        "Volume": "volume", "Dividends": "dividend", "Stock Splits": "split"}


def fetch(ticker, **kw):
    def go():
        h = yf.Ticker(ticker).history(auto_adjust=False, actions=True, **kw)
        if h is None or h.empty:
            raise ValueError("empty history")
        return h
    h = retry(go, what=ticker)
    h = h.rename(columns=COLS)[[c for c in COLS.values() if c in h.rename(columns=COLS).columns]]
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    h.index.name = "date"
    return h[~h.index.duplicated(keep="last")].sort_index()


def update(ticker, full=False):
    path = DATA / "prices" / f"{safe_name(ticker)}.csv"
    old = pd.read_csv(path, index_col="date", parse_dates=True) if path.exists() else None
    if old is None or old.empty or full:
        new = fetch(ticker, period="max")
        merged = new
    else:
        start = (old.index.max() - pd.Timedelta(days=15)).strftime("%Y-%m-%d")
        new = fetch(ticker, start=start)
        events = new[["dividend", "split"]].fillna(0).abs().sum().sum() if {"dividend", "split"} <= set(new.columns) else 0
        if events > 0:
            new = fetch(ticker, period="max")
            merged = new
        else:
            merged = pd.concat([old[old.index < new.index.min()], new]).sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]
    write_csv(merged.round(6), path)
    return len(merged), merged.index.max().date()


def main():
    full = os.environ.get("FULL") == "1" or dt.date.today().weekday() == 5
    wl = watchlist()
    failed = 0
    for t in wl["ticker"]:
        try:
            n, last = update(t, full=full)
            log_status("prices", t, True, n, f"last {last}")
            print(f"{t}: {n} rows, last {last}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            log_status("prices", t, False, 0, e)
            print(f"{t}: FAILED {e}")
        time.sleep(2)  # be polite: one ticker every few seconds
    trim_status()
    print(f"done, {failed} failed of {len(wl)}")


if __name__ == "__main__":
    main()
