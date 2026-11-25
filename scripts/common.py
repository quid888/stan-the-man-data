"""Shared helpers: paths, watchlist, safe CSV writes, run status, polite retries."""
import csv
import datetime as dt
import os
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
CONFIG = Path(os.environ.get("CONFIG_DIR", ROOT / "config"))


def watchlist():
    return pd.read_csv(CONFIG / "watchlist.csv", dtype=str, keep_default_na=False)


def safe_name(ticker: str) -> str:
    """File-system safe version of a Yahoo ticker (^SSMI -> _SSMI, GC=F -> GC_F)."""
    return ticker.replace("^", "_").replace("=", "_").replace("/", "_")


def write_csv(df: pd.DataFrame, path: Path, index=True):
    """Write atomically so a failed run never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=index)
    os.replace(tmp, path)


def now_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def log_status(source: str, item: str, ok: bool, rows: int = 0, note: str = ""):
    """One line per item per run in data/status.csv (newest last), so stale or failing feeds are visible."""
    path = DATA / "status.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["run_utc", "source", "item", "ok", "rows", "note"])
        w.writerow([now_utc(), source, item, int(ok), rows, str(note)[:200]])


def trim_status(keep_days=60):
    path = DATA / "status.csv"
    if not path.exists():
        return
    s = pd.read_csv(path)
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)).strftime("%Y-%m-%d")
    write_csv(s[s["run_utc"] >= cutoff], path, index=False)


def retry(fn, tries=4, base_sleep=8, what=""):
    """Call fn with exponential back-off; Yahoo rate-limits cloud IPs, so be slow and patient."""
    err = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 (any network or parsing error)
            err = e
            time.sleep(base_sleep * (2 ** i))
    raise RuntimeError(f"{what}: {err}")
