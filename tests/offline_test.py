"""Offline test: builds synthetic data in a temp folder and runs the parsers and risk.py on it.
Run: python tests/offline_test.py  (needs no network)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

tmp = Path(tempfile.mkdtemp())
os.environ["DATA_DIR"] = str(tmp)

from common import safe_name, watchlist  # noqa: E402
from factors import parse_french  # noqa: E402
from macro import parse_fred, parse_snb  # noqa: E402

# --- parsers ---
french = """This file was created using the 202509 Bloomberg database.
Missing data are indicated by -99.99.

,Mkt-RF,SMB,HML,RMW,CMA,RF
199007,   0.86,   0.18,  -0.75,   0.32,  -0.10,   0.68
199008, -11.52,   1.41,   0.33,   0.71,   0.62,   0.66
199009,  -3.10,  -0.20,   0.10,   0.50,   0.10,   0.60

 Annual Factors: January-December
,Mkt-RF,SMB,HML,RMW,CMA,RF
 1991,  17.5,  -2.0,   3.1,   1.0,   0.5,   5.5
"""
f = parse_french(french)
assert list(f.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"], f.columns
assert len(f) == 3 and str(f.index[0].date()) == "1990-07-31", f
fred = "observation_date,DGS10\n2026-11-20,4.41\n2026-11-21,.\n2026-11-24,4.39\n"
fr = parse_fred(fred)
assert len(fr) == 2 and abs(fr["value"].iloc[-1] - 4.39) < 1e-9
snb = '"CubeId";"rendoblim"\n"PublishingDate";"2026-11-24 14:30"\n\n"Date";"D0";"Value"\n"2026-10";"1J";"0.12"\n"2026-10";"10J";"0.58"\n'
sn = parse_snb(snb)
assert len(sn) == 2 and sn["series"].tolist() == ["1J", "10J"], sn
print("parsers ok")

# --- synthetic prices ---
rng = np.random.default_rng(1)
days = pd.bdate_range("2005-01-03", "2026-11-24")
wl = watchlist()
(tmp / "prices").mkdir(parents=True)
market = rng.normal(0.0003, 0.011, len(days))
for row in wl.itertuples():
    if row.role == "fx":
        level = {"CHF=X": 0.84, "EURCHF=X": 0.93, "GBPCHF=X": 1.07}[row.ticker]
        px = level * np.exp(np.cumsum(rng.normal(0, 0.004, len(days))))
    else:
        b = rng.uniform(0.5, 1.2)
        px = 100 * np.exp(np.cumsum(b * market + rng.normal(0, 0.01, len(days))))
        if row.price_unit == "pence":
            px = px * 100
    df = pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "adj_close": px,
                       "volume": 1000, "dividend": 0.0, "split": 0.0}, index=days.rename("date"))
    df.to_csv(tmp / "prices" / f"{safe_name(row.ticker)}.csv")
# --- synthetic factors ---
(tmp / "factors").mkdir()
months = pd.date_range("2005-01-31", "2026-10-31", freq="ME")
for region in ("europe", "developed"):
    pd.DataFrame(rng.normal(0.5, 4, (len(months), 6)), index=months.rename("date"),
                 columns=["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]).to_csv(tmp / "factors" / f"{region}_5f_monthly.csv")
    pd.DataFrame(rng.normal(0.5, 4, (len(months), 1)), index=months.rename("date"),
                 columns=["WML"]).to_csv(tmp / "factors" / f"{region}_mom_monthly.csv")

r = subprocess.run([sys.executable, str(ROOT / "scripts" / "risk.py")], capture_output=True, text=True,
                   env={**os.environ, "DATA_DIR": str(tmp)})
print(r.stdout, r.stderr[-2000:])
assert r.returncode == 0
m = pd.read_csv(tmp / "derived" / "risk_metrics.csv", index_col=0)
fl = pd.read_csv(tmp / "derived" / "factor_loadings.csv", index_col=0)
c = pd.read_csv(tmp / "derived" / "correlation_weekly_5y.csv", index_col=0)
assert "NESN.SW" in m.index and m.loc["NESN.SW", "vol_5y"] > 0
assert m["beta_spi_5y"].between(-1, 3).all()
assert "beta_Mkt-RF" in fl.columns and len(fl) > 5
print(m.loc[["NESN.SW", "ULVR.L", "GC=F", "URTH"], ["cagr_5y", "vol_5y", "max_drawdown_5y", "beta_spi_5y", "corr_gold_5y"]])
print(fl.iloc[:3, :8])
print(c.round(2).iloc[:4, :4])
print("risk ok:", tmp)
