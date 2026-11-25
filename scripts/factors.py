"""Fama-French factors from Kenneth French's data library (free, updated monthly).

data/factors/<name>.csv: date, then factor returns in percent (as published), e.g.
  europe_5f_monthly: Mkt-RF, SMB, HML, RMW, CMA, RF
  europe_mom_monthly: WML
Important: the international factors are in US dollars, with the US one-month T-bill as RF.
Regress stock returns converted to USD on them (scripts/risk.py does this).
"""
import io
import os
import re
import sys
import zipfile

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
from common import DATA, log_status, retry, write_csv  # noqa: E402

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FILES = {
    "europe_5f_monthly": "Europe_5_Factors_CSV.zip",
    "europe_mom_monthly": "Europe_Mom_Factor_CSV.zip",
    "developed_5f_monthly": "Developed_5_Factors_CSV.zip",
    "developed_mom_monthly": "Developed_Mom_Factor_CSV.zip",
    "europe_5f_daily": "Europe_5_Factors_Daily_CSV.zip",
    "europe_mom_daily": "Europe_Mom_Factor_Daily_CSV.zip",
}


def parse_french(text: str) -> pd.DataFrame:
    """Keep the first block of rows dated YYYYMM or YYYYMMDD; the annual block that follows is dropped."""
    header, rows, started = None, [], False
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if not parts or not parts[0]:
            if started:
                break
            if len(parts) > 1 and any(parts[1:]):
                header = parts[1:]  # header line starts with an empty first cell
            continue
        if re.fullmatch(r"\d{6}|\d{8}", parts[0]):
            started = True
            rows.append(parts)
        elif started:
            break
    if not rows or header is None:
        raise ValueError("no data block found")
    width = min(len(header), len(rows[0]) - 1)
    df = pd.DataFrame([r[1:1 + width] for r in rows], columns=header[:width],
                      index=[r[0] for r in rows]).apply(pd.to_numeric, errors="coerce")
    fmt = "%Y%m" if len(rows[0][0]) == 6 else "%Y%m%d"
    idx = pd.to_datetime(df.index, format=fmt)
    if fmt == "%Y%m":
        idx = idx + pd.offsets.MonthEnd(0)
    df.index = idx
    df.index.name = "date"
    df = df.replace([-99.99, -999], pd.NA)
    df.columns = [c.strip() for c in df.columns]
    return df


def download(name, file):
    def go():
        r = requests.get(BASE + file, timeout=60)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        inner = z.namelist()[0]
        return z.read(inner).decode("latin-1")
    return parse_french(retry(go, tries=3, base_sleep=5, what=name))


def main():
    for name, file in FILES.items():
        try:
            df = download(name, file)
            write_csv(df, DATA / "factors" / f"{name}.csv")
            log_status("factors", name, True, len(df), f"last {df.index.max().date()}")
            print(name, len(df), df.index.max().date())
        except Exception as e:  # noqa: BLE001
            log_status("factors", name, False, 0, e)
            print(name, "FAILED", e)


if __name__ == "__main__":
    main()
