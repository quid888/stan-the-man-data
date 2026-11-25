"""Offline checks for the regime and scoring models: published reference values, formulas and the Shiller parser.
Run: python tests/model_test.py  (needs no network)."""
import io
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from regime import parse_shiller, probit  # noqa: E402
from scores import points  # noqa: E402

# NY Fed yield-curve model against the NY Fed's own published readings (prob_rec.pdf):
# spread 0.22053 (Nov 2025) -> 25.0507% for Nov 2026; spread 0.62615 (Apr 2026) -> 17.6275% for Apr 2027
assert abs(probit(0.22053) * 100 - 25.0507) < 0.01, probit(0.22053)
assert abs(probit(0.62615) * 100 - 17.6275) < 0.01, probit(0.62615)
print("NY Fed probit matches published values")

# Beneish M-score of a firm whose indices are all neutral (1.0) with zero accruals
m = -4.84 + 0.92 + 0.528 + 0.404 + 0.892 + 0.115 - 0.172 + 4.679 * 0 - 0.327
assert abs(m - (-2.48)) < 1e-9, m
print("Beneish neutral firm = -2.48 (below the -1.78 manipulation threshold)")

# piecewise scales: ends clamp, middle interpolates, descending scales work
assert points("roic_avg", 100) == 10 and points("roic_avg", -5) == 0
assert abs(points("roic_avg", 10) - 4.5) < 1e-9
assert points("ev_ebitda", 6) == 10 and points("ev_ebitda", 20) == 0
assert points("roic_avg", float("nan")) is None
print("scales ok")

# Shiller parser on a synthetic sheet laid out like ie_data.xls (header text over several rows, dates as 1871.01)
try:
    import openpyxl  # noqa: F401
    rows = [["Stock market data"] + [None] * 14,
            [None] * 15,
            ["Date", "S&P Comp.", "Dividend", "Earnings", "Consumer Price", "Date", "Long Interest Rate GS10", "Real Price",
             "Real Dividend", "Real Total Return Price", "Real Earnings", "Real TR Scaled Earnings",
             "Cyclically Adjusted Price Earnings Ratio P/E10 or CAPE", "Cyclically Adjusted Total Return Price Earnings Ratio TR CAPE",
             "Excess CAPE Yield"]]
    rng = np.random.default_rng(3)
    n = 400
    tr = 100 * np.exp(np.cumsum(rng.normal(0.005, 0.04, n)))
    cape = np.r_[np.full(120, np.nan), rng.uniform(8, 40, n - 120)]
    for i in range(n):
        y, mth = 1871 + i // 12, i % 12 + 1
        date = float(f"{y}.{mth:02d}") if mth != 10 else float(f"{y}.1")
        rows.append([date, 4.0 + i * 0.01, 0.2, 0.3, 12.0, None, 5.0, 80.0, 4.0, tr[i], 6.0, 6.0, cape[i], cape[i], 0.03])
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="Data", header=False, index=False)
    out = parse_shiller(buf.getvalue())
    assert len(out) == n, len(out)
    assert str(out["date"].iloc[9].date()) == "1871-10-01", out["date"].iloc[9]   # 1871.1 is October
    assert out["cape"].notna().sum() == n - 120
    assert abs(out["excess_cape_yield"].iloc[-1] - 3.0) < 1e-9          # stored as a fraction, reported in %
    print("Shiller parser ok")
except ImportError:
    print("openpyxl missing: Shiller parser test skipped")

print("model tests passed")
