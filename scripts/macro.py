"""Rates and inflation: FRED (no key needed for the CSV export) and the SNB data portal.

data/macro/fred_<series>.csv   date, value
data/macro/snb_rendoblim.csv   date, series, value   (yields on Swiss Confederation bonds by maturity)
"""
import io
import os
import sys

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
SNB_CUBES = {"rendoblim": "Yields on Swiss Confederation bonds by maturity"}


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


def get(url):
    def go():
        r = requests.get(url, timeout=(10, 30), headers={"User-Agent": "stan-the-man-data (personal research)"})
        r.raise_for_status()
        return r.text
    return retry(go, tries=2, base_sleep=3, what=url)


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

    for sid, desc in FRED.items():
        try:
            df = parse_fred(get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"))
            write_csv(df, DATA / "macro" / f"fred_{sid}.csv")
            log_status("macro", sid, True, len(df), f"last {df.index.max().date()}")
            print(sid, len(df), df.index.max().date())
        except Exception as e:  # noqa: BLE001
            log_status("macro", sid, False, 0, e)
            print(sid, "FAILED", e)


if __name__ == "__main__":
    main()
