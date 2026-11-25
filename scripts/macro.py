"""Rates and inflation: SNB data portal, US Treasury, and FRED when it answers.

data/macro/snb_<cube>.csv        date, series, value   Swiss Confederation bond yields by maturity
                                 (rendoblim: monthly, old method, ends July 2025; rendoblid: current series)
data/macro/ust_yield_curve.csv   US Treasury par yield curve, daily, % (1 Mo ... 30 Yr)
data/macro/fred_<series>.csv     date, value (FRED often does not answer GitHub's servers; given 45 seconds)
"""
import datetime as dt
import io
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
from common import DATA, log_status, retry, write_csv  # noqa: E402

FRED = {
    "DGS10": "US 10-year Treasury yield, daily, %",
    "DFF": "US federal funds effective rate, daily, %",
    "DTB3": "US 3-month Treasury bill, daily, %",
    "IRLTLT01CHM156N": "Switzerland 10-year government bond yield, monthly, % (OECD)",
    "IR3TIB01CHM156N": "Switzerland 3-month interbank rate, monthly, % (OECD)",
    "CHECPIALLMINMEI": "Switzerland consumer price index, monthly (OECD)",
    "IRLTLT01DEM156N": "Germany 10-year government bond yield, monthly, % (OECD)",
}
SNB_CUBES = {"rendoblim": "Swiss Confederation bond yields, monthly, method until July 2025",
             "rendoblid": "Swiss Confederation bond yields, current series"}
UST = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
       "{y}/all?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv")
FRED_BUDGET_SECONDS = 45


def parse_fred(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")  # FRED marks gaps with "."
    return df.dropna().set_index("date")


def parse_snb(text: str) -> pd.DataFrame:
    """SNB CSV: a few metadata lines, a blank line, then 'Date';'D0';'Value' (semicolon separated)."""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.replace('"', "").startswith("Date;"))
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), sep=";")
    df.columns = [c.strip('"') for c in df.columns]
    df = df.rename(columns={"Date": "date", "Value": "value"})
    series_col = [c for c in df.columns if c not in ("date", "value")]
    df["series"] = df[series_col].astype(str).agg("|".join, axis=1) if series_col else "all"
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df[["date", "series", "value"]].dropna()


def get(url, timeout=(10, 30), tries=2):
    def go():
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "stan-the-man-data (personal research)"})
        r.raise_for_status()
        return r.text
    return retry(go, tries=tries, base_sleep=3, what=url)


def treasury():
    """Daily US par yield curve; the first run backfills from 2000, later runs refresh this year and last."""
    path = DATA / "macro" / "ust_yield_curve.csv"
    this_year = dt.date.today().year
    years = range(this_year - 1, this_year + 1) if path.exists() else range(2000, this_year + 1)
    frames = []
    for y in years:
        df = pd.read_csv(io.StringIO(get(UST.format(y=y), timeout=(10, 40))))
        df["Date"] = pd.to_datetime(df["Date"])
        frames.append(df.set_index("Date"))
    new = pd.concat(frames)
    if path.exists():
        old = pd.read_csv(path, index_col="Date", parse_dates=True)
        new = pd.concat([old[~old.index.isin(new.index)], new])
    new = new.sort_index()
    write_csv(new, path)
    return new


def main():
    # SNB first: the Swiss data matters most, and FRED can be slow from cloud servers
    for cube, desc in SNB_CUBES.items():
        try:
            df = parse_snb(get(f"https://data.snb.ch/api/cube/{cube}/data/csv/en"))
            write_csv(df, DATA / "macro" / f"snb_{cube}.csv", index=False)
            log_status("macro", f"snb {cube}", True, len(df), f"last {df['date'].max()}")
            print(cube, len(df), df["date"].max())
        except Exception as e:  # noqa: BLE001
            log_status("macro", f"snb {cube}", False, 0, e)
            print(cube, "FAILED", e)

    try:
        ust = treasury()
        log_status("macro", "us treasury curve", True, len(ust), f"last {ust.index.max().date()}")
        print("treasury", len(ust), ust.index.max().date())
    except Exception as e:  # noqa: BLE001
        log_status("macro", "us treasury curve", False, 0, e)
        print("treasury FAILED", e)

    deadline = time.time() + FRED_BUDGET_SECONDS
    for sid, desc in FRED.items():
        if time.time() > deadline:
            log_status("macro", sid, False, 0, "skipped: FRED time budget used up")
            continue
        try:
            df = parse_fred(get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=(5, 10), tries=1))
            write_csv(df, DATA / "macro" / f"fred_{sid}.csv")
            log_status("macro", sid, True, len(df), f"last {df.index.max().date()}")
            print(sid, len(df), df.index.max().date())
        except Exception as e:  # noqa: BLE001
            log_status("macro", sid, False, 0, e)
            print(sid, "FAILED", e)


if __name__ == "__main__":
    main()
