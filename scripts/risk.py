"""Derived risk numbers, rebuilt after every price run. Everything is measured in Swiss francs.

data/derived/prices_chf.csv          total-return price (adj_close) of every non-FX ticker converted to CHF, from 2000
data/derived/risk_metrics.csv        per ticker: return, volatility, drawdowns, VaR and expected shortfall,
                                     beta to the SPI and to MSCI World, downside beta, correlation with gold,
                                     worst calendar year, 2008 and 2022 returns
data/derived/correlation_weekly_5y.csv   weekly-return correlations: holdings, candidate, gold, SPI, MSCI World
data/derived/factor_loadings.csv     Fama-French five factors plus momentum, last 60 months (USD returns, as the
                                     factors are in USD); alpha a year, loadings, t-stats, R-squared
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from common import DATA, log_status, safe_name, watchlist, write_csv  # noqa: E402

FX = {"USD": "CHF=X", "EUR": "EURCHF=X", "GBP": "GBPCHF=X"}
SPI, SMI, WORLD, GOLD = "^SSHI", "^SSMI", "URTH", "GC=F"
TD = 252


def load(ticker):
    p = DATA / "prices" / f"{safe_name(ticker)}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, index_col="date", parse_dates=True)
    col = "adj_close" if "adj_close" in df.columns and df["adj_close"].notna().any() else "close"
    return df[col].dropna()


def in_chf(row, fx):
    s = load(row.ticker)
    if s is None or s.empty:
        return None
    if row.price_unit == "pence":
        s = s / 100.0
    if row.currency == "CHF":
        return s
    rate = fx.get(row.currency)
    if rate is None:
        return None
    r = rate.reindex(s.index.union(rate.index)).ffill().reindex(s.index)
    return (s * r).dropna()


def max_dd(s):
    peak = s.cummax()
    dd = s / peak - 1
    return dd.min(), dd.idxmin()


def var_es(r, q=0.05):
    r = r.dropna()
    if len(r) < 30:
        return np.nan, np.nan
    v = r.quantile(q)
    return v, r[r <= v].mean()


def beta(y, x, downside=False):
    d = pd.concat([y, x], axis=1).dropna()
    if downside:
        d = d[d.iloc[:, 1] < 0]
    if len(d) < 26 or d.iloc[:, 1].var() == 0:
        return np.nan
    return np.cov(d.iloc[:, 0], d.iloc[:, 1])[0, 1] / d.iloc[:, 1].var()


def cal_return(s, year):
    y = s[s.index.year == year]
    prev = s[s.index.year == year - 1]
    if y.empty or prev.empty:
        return np.nan
    return y.iloc[-1] / prev.iloc[-1] - 1


def ols(y, X):
    X1 = np.column_stack([np.ones(len(X)), X])
    coef, *_ = np.linalg.lstsq(X1, y, rcond=None)
    resid = y - X1 @ coef
    dof = len(y) - X1.shape[1]
    s2 = resid @ resid / dof
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X1.T @ X1)))
    r2 = 1 - (resid @ resid) / ((y - y.mean()) @ (y - y.mean()))
    return coef, coef / se, r2


def main():
    wl = watchlist()
    fx = {cur: load(t) for cur, t in FX.items()}
    fx = {k: v for k, v in fx.items() if v is not None}
    usdchf = fx.get("USD")
    chf = {}
    for row in wl.itertuples():
        if row.role == "fx":
            continue
        s = in_chf(row, fx)
        if s is not None and len(s) > 10:
            chf[row.ticker] = s
    if not chf:
        print("no price data yet")
        return
    wide = pd.DataFrame(chf).sort_index()
    write_csv(wide[wide.index >= "2000-01-01"].round(6), DATA / "derived" / "prices_chf.csv")

    end = wide.index.max()
    start5 = end - pd.DateOffset(years=5)
    weekly = wide.resample("W-FRI").last().pct_change(fill_method=None)
    monthly = wide.resample("ME").last().pct_change(fill_method=None)
    bench_spi = SPI if SPI in wide else SMI
    rows = []
    for t in wide.columns:
        s = wide[t].dropna()
        d = s.pct_change(fill_method=None).dropna()
        d5 = d[d.index >= start5]
        s5 = s[s.index >= start5]
        w5 = weekly[t][weekly.index >= start5]
        m10 = monthly[t][monthly.index >= end - pd.DateOffset(years=10)]
        yrs5 = (s5.index[-1] - s5.index[0]).days / 365.25 if len(s5) > 1 else np.nan
        dd_all, dd_all_date = max_dd(s)
        dd5, _ = max_dd(s5) if len(s5) > 1 else (np.nan, None)
        v1, e1 = var_es(d5)
        vm, em = var_es(m10)
        years = [y for y in range(s.index.min().year + 1, end.year) if (s.index.year == y).any()]
        cal = {y: cal_return(s, y) for y in years}
        worst_year = min(cal, key=cal.get) if cal else None
        rows.append({
            "ticker": t, "first_date": s.index.min().date(), "last_date": s.index.max().date(),
            "cagr_5y": (s5.iloc[-1] / s5.iloc[0]) ** (1 / yrs5) - 1 if yrs5 and yrs5 > 0.5 else np.nan,
            "vol_5y": d5.std() * np.sqrt(TD),
            "max_drawdown_all": dd_all, "max_drawdown_all_trough": dd_all_date.date() if dd_all_date is not None else None,
            "max_drawdown_5y": dd5,
            "var95_1d": v1, "es95_1d": e1, "var95_1m": vm, "es95_1m": em,
            "beta_spi_5y": beta(w5, weekly[bench_spi][weekly.index >= start5]) if bench_spi in weekly else np.nan,
            "downside_beta_spi_5y": beta(w5, weekly[bench_spi][weekly.index >= start5], True) if bench_spi in weekly else np.nan,
            "beta_world_5y": beta(w5, weekly[WORLD][weekly.index >= start5]) if WORLD in weekly else np.nan,
            "corr_gold_5y": w5.corr(weekly[GOLD][weekly.index >= start5]) if GOLD in weekly else np.nan,
            "corr_spi_5y": w5.corr(weekly[bench_spi][weekly.index >= start5]) if bench_spi in weekly else np.nan,
            "worst_year": worst_year, "worst_year_return": cal.get(worst_year) if worst_year else np.nan,
            "return_2008": cal.get(2008, np.nan), "return_2022": cal.get(2022, np.nan),
        })
    metrics = pd.DataFrame(rows).set_index("ticker")
    write_csv(metrics.round(4), DATA / "derived" / "risk_metrics.csv")

    keep = [t for t in wl[wl["role"].isin(["holding", "candidate"])]["ticker"] if t in weekly] + \
           [t for t in (GOLD, bench_spi, WORLD) if t in weekly]
    corr = weekly.loc[weekly.index >= start5, keep].corr()
    write_csv(corr.round(3), DATA / "derived" / "correlation_weekly_5y.csv")

    factors_out = []
    ff = {}
    for region in ("europe", "developed"):
        f5 = DATA / "factors" / f"{region}_5f_monthly.csv"
        fm = DATA / "factors" / f"{region}_mom_monthly.csv"
        if f5.exists() and fm.exists():
            a = pd.read_csv(f5, index_col="date", parse_dates=True)
            b = pd.read_csv(fm, index_col="date", parse_dates=True)
            ff[region] = a.join(b, how="inner") / 100.0
    if ff and usdchf is not None:
        usd_rate = usdchf.resample("ME").last()
        for row in wl.itertuples():
            if row.role in ("fx",) or row.ticker not in wide:
                continue
            region = "developed" if row.currency == "USD" else "europe"
            if region not in ff:
                continue
            f = ff[region]
            px_usd = wide[row.ticker].resample("ME").last() / usd_rate
            r = px_usd.pct_change(fill_method=None)
            d = pd.concat([r.rename("r"), f], axis=1, join="inner").dropna().tail(60)
            if len(d) < 36:
                continue
            xcols = [c for c in d.columns if c not in ("r", "RF")]
            y = (d["r"] - d["RF"]).to_numpy()
            coef, tstat, r2 = ols(y, d[xcols].to_numpy())
            out = {"ticker": row.ticker, "region": region, "months": len(d),
                   "first_month": d.index.min().date(), "last_month": d.index.max().date(),
                   "alpha_a_year": (1 + coef[0]) ** 12 - 1, "alpha_t": tstat[0], "r2": r2}
            for i, c in enumerate(xcols, start=1):
                out[f"beta_{c}"] = coef[i]
                out[f"t_{c}"] = tstat[i]
            factors_out.append(out)
    if factors_out:
        write_csv(pd.DataFrame(factors_out).set_index("ticker").round(4), DATA / "derived" / "factor_loadings.csv")
    log_status("risk", "derived", True, len(metrics), f"{len(factors_out)} factor regressions")
    print(f"risk metrics for {len(metrics)} tickers, {len(factors_out)} factor regressions")


if __name__ == "__main__":
    main()
