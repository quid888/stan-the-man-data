"""Market regime from key indexes: what the market as a whole is pricing, and what that implies.

Outputs (all in data/):
  macro/shiller_us.csv            Robert Shiller's monthly US data (price, earnings, CPI, GS10, CAPE, TR CAPE, excess CAPE yield)
  derived/regime.csv              one row per indicator: value, percentile, reading, derived result, stance points
  derived/regime_summary.csv      one row: valuation state, fragility score, regime, hurdle rate, required margin of safety
  derived/regime_history.csv      monthly history of the main indicators since 1990, for charts and back-checks

Models used (sources in the framework doc):
  * 10-year real return of US stocks from the excess CAPE yield and from 1/CAPE, fitted by OLS on Shiller's data
    (forward 10-year real total return on the right-hand side), with an 80% band from the residuals.
  * New York Fed probit: P(recession within 12 months) = Phi(-0.5333 - 0.6330 * (10y - 3m spread, % points)),
    monthly average spread. Here with constant-maturity Treasury yields, so a close approximation of the NY Fed number.
  * Stress, trend and concentration from market prices: VIX, high-yield vs Treasury bond ETFs, 200-day averages,
    equal-weight vs cap-weight S&P 500 (Burry's passive-concentration argument), small vs large, value vs growth.

Run: python scripts/regime.py [--fetch]   (--fetch downloads Shiller's file again; otherwise the saved copy is used)
"""
import io
import math
import re
import sys

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from common import DATA, log_status, now_utc, retry, safe_name, write_csv  # noqa: E402

SHILLER_PAGES = ["https://shillerdata.com/", "http://www.econ.yale.edu/~shiller/data.htm"]
UA = {"User-Agent": "Mozilla/5.0 (stan-the-man-data research pipeline)"}

NYFED_A, NYFED_B = -0.5333, -0.6330      # NY Fed yield-curve probit, estimated on 1959-2009 data
CH_INFLATION = 1.0                        # % a year, plan assumption (Portfolio plan, Key facts)
CONCENTRATION_PREMIUM = 3.0               # % points a single stock must beat the index by (Munger's opportunity cost)
REQUIRED_RETURN_FLOOR = 7.0               # % nominal, floor for the implied-growth test in scores.py


# ---------- Shiller data ----------
KNOWN_SHILLER_FILES = [
    # GoDaddy-hosted copy linked from shillerdata.com (seen in 2025); the page is searched first for the current link
    "https://img1.wsimg.com/blobby/go/e5e77e0b-59d1-44d9-ab25-4763ac982e53/downloads/c838cbf2-d3d1-49fe-b957-08441db9b674/ie_data.xls",
    "http://www.econ.yale.edu/~shiller/data/ie_data.xls",
]


def shiller_candidates():
    """Every ie_data.xls link on Shiller's pages, newest page first, then the known file locations."""
    urls = []
    for page in SHILLER_PAGES:
        try:
            html = requests.get(page, headers=UA, timeout=20).text.replace("&amp;", "&")
            for u in re.findall(r"""(https?://[^"'\s<>]*ie_data\.xls[^"'\s<>]*|/[^"'\s<>]*ie_data\.xls[^"'\s<>]*)""", html, flags=re.I):
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("/"):
                    u = re.match(r"https?://[^/]+", page).group(0) + u
                if u not in urls:
                    urls.append(u)
        except Exception as e:  # noqa: BLE001
            print("page", page, "failed", e)
    return urls + [u for u in KNOWN_SHILLER_FILES if u not in urls]


def download_shiller():
    """First candidate whose bytes are really an Excel file (OLE2 for .xls, zip for .xlsx)."""
    tried = []
    for url in shiller_candidates():
        try:
            r = requests.get(url, headers=UA, timeout=60, allow_redirects=True)
            head = r.content[:8]
            if r.ok and (head.startswith(b"\xd0\xcf\x11\xe0") or head.startswith(b"PK")):
                return url, r.content
            tried.append(f"{url[:90]} -> {r.status_code} {r.headers.get('content-type', '')[:30]} {head!r}")
        except Exception as e:  # noqa: BLE001
            tried.append(f"{url[:90]} -> {e}")
    raise RuntimeError("no Excel file at any Shiller link: " + " | ".join(tried))


def parse_shiller(xls_bytes):
    """Robust parse of Shiller's 'Data' sheet: header text is spread over the top rows, data start below."""
    raw = pd.read_excel(io.BytesIO(xls_bytes), sheet_name="Data", header=None)
    start = None
    for i in range(min(30, len(raw))):
        v = str(raw.iat[i, 0])
        if re.fullmatch(r"18\d\d\.\d+", v.strip()):
            start = i
            break
    if start is None:
        raise ValueError("no data rows found in Shiller sheet")
    head = [" ".join(str(raw.iat[r, c]) for r in range(start) if pd.notna(raw.iat[r, c])) for c in range(raw.shape[1])]

    def col(*must, avoid=()):
        for c, h in enumerate(head):
            if all(m.lower() in h.lower() for m in must) and not any(a.lower() in h.lower() for a in avoid):
                return c
        return None

    cols = {"date": 0, "price": 1, "dividend": 2, "earnings": 3, "cpi": 4,
            "gs10": col("GS10"), "real_tr_price": col("Total Return", "Price"),
            "cape": col("CAPE", avoid=("TR CAPE", "Excess", "Yield")), "tr_cape": col("TR CAPE"),
            "excess_cape_yield": col("Excess CAPE Yield")}
    body = raw.iloc[start:]
    out = pd.DataFrame({k: pd.to_numeric(body[c], errors="coerce") if c is not None else np.nan for k, c in cols.items() if k != "date"})
    d = body[0].astype(str).str.strip()
    ok = d.str.fullmatch(r"\d{4}\.\d+")
    out = out[ok.values]
    d = d[ok]
    year = d.str.slice(0, 4).astype(int)
    frac = d.str.split(".").str[1]
    month = frac.map(lambda s: int(s.ljust(2, "0")[:2]))   # 1871.1 means October
    out.insert(0, "date", pd.to_datetime(dict(year=year.values, month=month.values, day=1)).values)
    out = out.dropna(subset=["price"]).reset_index(drop=True)
    if out["excess_cape_yield"].isna().all() and out["cape"].notna().any():
        infl10 = (out["cpi"] / out["cpi"].shift(120)) ** (1 / 10) - 1
        out["excess_cape_yield"] = 1 / out["cape"] - (out["gs10"] / 100 - infl10)
    # Shiller stores the excess CAPE yield as a fraction (0.0056 = 0.56%); keep it in % for readability
    if out["excess_cape_yield"].abs().max() < 1:
        out["excess_cape_yield"] = out["excess_cape_yield"] * 100
    return out


def get_shiller(fetch):
    path = DATA / "macro" / "shiller_us.csv"
    if fetch or not path.exists():
        url, content = retry(download_shiller, tries=2, base_sleep=5, what="shiller")
        df = parse_shiller(content)
        write_csv(df, path, index=False)
        log_status("regime", "shiller ie_data", True, len(df), f"last {df['date'].max().date()} from {url[:80]}")
    return pd.read_csv(path, parse_dates=["date"])


def cape_models(sh):
    """OLS of the forward 10-year real total return on the excess CAPE yield and on 1/CAPE."""
    s = sh.set_index("date")
    tr = s["real_tr_price"]
    fwd = (tr.shift(-120) / tr) ** (1 / 10) - 1
    s = s.assign(fwd10=fwd * 100, ey=100 / s["cape"])
    res = {}
    for name, x in (("ecy", "excess_cape_yield"), ("ey", "ey")):
        d = s[[x, "fwd10"]].dropna()
        d = d[d.index >= "1881-01-01"]
        if len(d) < 240:
            continue
        b, a = np.polyfit(d[x], d["fwd10"], 1)
        resid = d["fwd10"] - (a + b * d[x])
        r2 = 1 - resid.var() / d["fwd10"].var()
        res[name] = dict(a=a, b=b, sd=resid.std(), r2=r2, n=len(d), x=x)
    return s, res


# ---------- market prices ----------
def px(ticker, col="adj_close"):
    p = DATA / "prices" / f"{safe_name(ticker)}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, index_col="date", parse_dates=True)
    c = col if col in df.columns and df[col].notna().any() else "close"
    s = df[c].dropna()
    return s if len(s) else None


def pct_rank(series, value):
    s = series.dropna()
    return float((s <= value).mean() * 100) if len(s) else np.nan


def probit(spread):
    z = NYFED_A + NYFED_B * spread
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def ordinal(x):
    n = int(round(x))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def clip(v, lo=0.0, hi=100.0):
    return float(min(hi, max(lo, v)))


def main():
    fetch = "--fetch" in sys.argv
    rows, comp = [], {}

    def add(group, key, name, value, unit, as_of, pct, reading, derived, source, points=None):
        rows.append(dict(group=group, key=key, name=name, value=None if value is None or (isinstance(value, float) and math.isnan(value)) else round(float(value), 4),
                         unit=unit, as_of=str(as_of)[:10], percentile=None if pct is None or (isinstance(pct, float) and math.isnan(pct)) else round(pct, 1),
                         reading=reading, derived=derived, fragility_points=None if points is None else round(points, 1), source=source))

    # 1. Valuation: Shiller CAPE and excess CAPE yield
    exp_real, band, cape_pct, ecy_now = np.nan, (np.nan, np.nan), np.nan, np.nan
    hist = pd.DataFrame()
    try:
        sh = get_shiller(fetch)
        s, models = cape_models(sh)
        last = s.dropna(subset=["cape"]).iloc[-1]
        cape_date = s.dropna(subset=["cape"]).index[-1]
        cape_pct = pct_rank(s["cape"], last["cape"])
        cape_pct_90 = pct_rank(s.loc["1990":, "cape"], last["cape"])
        ecy_now = last["excess_cape_yield"]
        # three estimates of the next ten years' real return, because linear fits pull extremes toward the average:
        # 1/CAPE regression, excess-CAPE-yield regression, and what followed when CAPE was within 10% of today's level
        est = {}
        for k, m in models.items():
            est[k] = m["a"] + m["b"] * last[m["x"]]
        near = s[(s["cape"] >= last["cape"] * 0.9) & (s["cape"] <= last["cape"] * 1.1)].dropna(subset=["fwd10"])
        if len(near) >= 12:
            est["analog"] = near["fwd10"].mean()
        if est:
            exp_real = float(np.median(list(est.values())))
            band = (float(min(est.values())), float(max(est.values())))
        episodes = ", ".join(sorted({str(y) for y in near.index.year})) if len(near) else "none"
        mtxt = "; ".join(f"{k.upper()} model R2 {m['r2']:.2f}" for k, m in models.items())
        names = {"ey": "1/CAPE model", "ecy": "excess-yield model", "analog": f"after similar CAPE levels ({len(near)} months: {episodes})"}
        add("valuation", "cape", "S&P 500 Shiller CAPE", last["cape"], "x", cape_date.date(), cape_pct,
            f"{ordinal(cape_pct)} percentile since 1881, {ordinal(cape_pct_90)} since 1990",
            f"US real return over the next 10 years: about {exp_real:.1f}% a year (median of three methods; " +
            "; ".join(f"{names[k]} {v:.1f}%" for k, v in est.items()) + f"). {mtxt}",
            "Robert Shiller, ie_data.xls")
        for k, v in est.items():
            add("valuation", f"exp10_{k}", f"10-year real return estimate: {names[k].split(' (')[0]}", v, "% a year", cape_date.date(), None,
                names[k], "One of the three estimates behind the hurdle rate", "Robert Shiller, ie_data.xls; regression in regime.py")
        ecy_pct = pct_rank(s["excess_cape_yield"], ecy_now)
        add("valuation", "ecy", "Excess CAPE yield (1/CAPE minus real 10-year yield)", ecy_now, "%", cape_date.date(), ecy_pct,
            f"{ordinal(ecy_pct)} percentile since 1881 (low = stocks expensive against bonds)",
            "The equity premium over real bonds that today's price leaves; the lower it is, the less stocks pay for their extra risk",
            "Robert Shiller, ie_data.xls")
        if "tr_cape" in s and s["tr_cape"].notna().any():
            add("valuation", "tr_cape", "S&P 500 total-return CAPE", s["tr_cape"].dropna().iloc[-1], "x", s["tr_cape"].dropna().index[-1].date(),
                pct_rank(s["tr_cape"], s["tr_cape"].dropna().iloc[-1]), "Corrects CAPE for the shift from dividends to buybacks",
                "Cross-check on CAPE", "Robert Shiller, ie_data.xls")
        hist = s.loc["1990":, ["cape", "excess_cape_yield", "fwd10"]].copy()
        hist.index = hist.index + pd.offsets.MonthEnd(0)
        if "ecy" in models:
            m = models["ecy"]
            hist["model10"] = m["a"] + m["b"] * hist["excess_cape_yield"]
        if "ey" in models:
            m = models["ey"]
            hist["model10_ey"] = m["a"] + m["b"] * (100 / hist["cape"])
        log_status("regime", "cape models", True, len(s), mtxt)
    except Exception as e:  # noqa: BLE001
        log_status("regime", "shiller", False, 0, e)
        print("Shiller FAILED", e)

    # 2. Cycle: yield curve and the NY Fed recession probability
    rec_prob = np.nan
    try:
        ust = pd.read_csv(DATA / "macro" / "ust_yield_curve.csv", index_col="Date", parse_dates=True).sort_index()
        spr = (ust["10 Yr"] - ust["3 Mo"]).dropna()
        monthly = spr.resample("ME").mean()
        last_month_avg = spr[spr.index >= spr.index.max() - pd.Timedelta(days=30)].mean()
        rec_prob = probit(last_month_avg) * 100
        add("cycle", "curve_10y3m", "US yield curve, 10 years minus 3 months", last_month_avg, "% pts", spr.index.max().date(),
            pct_rank(spr, last_month_avg), "Average of the last 30 days; negative = inverted",
            f"Chance of a US recession within 12 months about {rec_prob:.0f}% (NY Fed yield-curve model)",
            "US Treasury par yield curve; NY Fed probit (-0.5333, -0.6330)", points=clip((rec_prob - 10) / 40 * 100))
        comp["recession"] = clip((rec_prob - 10) / 40 * 100)
        s2 = (ust["10 Yr"] - ust["2 Yr"]).dropna()
        add("cycle", "curve_10y2y", "US yield curve, 10 years minus 2 years", s2.iloc[-1], "% pts", s2.index[-1].date(),
            pct_rank(s2, s2.iloc[-1]), "Positive after an inversion often comes late in the cycle", "Cross-check on the 10y-3m signal",
            "US Treasury par yield curve")
        add("cycle", "ust10", "US 10-year Treasury yield", ust["10 Yr"].dropna().iloc[-1], "%", ust.index.max().date(),
            pct_rank(ust["10 Yr"], ust["10 Yr"].dropna().iloc[-1]), "Since 2000", "The discount rate for every asset priced in dollars",
            "US Treasury par yield curve")
        rp = monthly.apply(probit) * 100
        h2 = pd.DataFrame({"curve_10y3m": monthly, "recession_prob": rp})
        hist = hist.join(h2, how="outer") if len(hist) else h2
    except Exception as e:  # noqa: BLE001
        log_status("regime", "yield curve", False, 0, e)

    # 3. Stress: VIX and MOVE
    vix = px("^VIX", "close")
    if vix is not None:
        v = vix.iloc[-1]
        pts = clip((v - 14) / 16 * 100)
        comp["volatility"] = pts
        reading = "complacent (below 13)" if v < 13 else "calm" if v < 20 else "nervous" if v < 30 else "fear (above 30)"
        add("stress", "vix", "VIX, expected S&P 500 volatility", v, "pts", vix.index[-1].date(), pct_rank(vix, v), reading,
            "Fear above 30 has historically been a better time to buy than to sell; below 13 nobody is paid for risk",
            "Yahoo Finance via stan-the-man-data (^VIX)", points=pts)
        hist = hist.join(vix.resample("ME").mean().rename("vix"), how="outer")
    move = px("^MOVE", "close")
    if move is not None:
        add("stress", "move", "MOVE, expected Treasury volatility", move.iloc[-1], "pts", move.index[-1].date(), pct_rank(move, move.iloc[-1]),
            "Bond-market fear; high readings preceded the 2022 rate shock and the 2023 bank failures", "Watch with VIX: both high = forced selling",
            "Yahoo Finance via stan-the-man-data (^MOVE)")

    # 4. Credit: high-yield bonds against Treasuries (price proxy for credit spreads)
    hyg, ief = px("HYG"), px("IEF")
    if hyg is not None and ief is not None:
        ratio = (hyg / ief).dropna()
        dd = (1 - ratio.iloc[-1] / ratio[ratio.index >= ratio.index[-1] - pd.Timedelta(days=365)].max()) * 100
        chg3 = (ratio.iloc[-1] / ratio[ratio.index <= ratio.index[-1] - pd.Timedelta(days=91)].iloc[-1] - 1) * 100
        pts = clip(dd / 6 * 100)
        comp["credit"] = pts
        add("credit", "hy_vs_treasury", "High-yield vs Treasury bonds (HYG/IEF, total return)", dd, "% below 1-year high", ratio.index[-1].date(),
            None, f"{chg3:+.1f}% over 3 months", "A fall of 5% or more from the high has marked widening credit spreads; Burry's 2005-07 signal was credit, not stocks",
            "Yahoo Finance via stan-the-man-data (HYG, IEF)", points=pts)
        hist = hist.join((ratio / ratio.iloc[0] * 100).resample("ME").last().rename("hy_vs_treasury"), how="outer")

    # 5. Trend: main indexes against their 200-day average
    below = []
    for t, nm in (("^GSPC", "S&P 500"), ("^SSHI", "Swiss Performance Index"), ("URTH", "MSCI World"), ("^STOXX50E", "Euro Stoxx 50")):
        s_ = px(t)
        if s_ is None or len(s_) < 220:
            continue
        ma = s_.rolling(200).mean().iloc[-1]
        gap = (s_.iloc[-1] / ma - 1) * 100
        r12 = (s_.iloc[-1] / s_[s_.index <= s_.index[-1] - pd.Timedelta(days=365)].iloc[-1] - 1) * 100
        if t != "^STOXX50E":
            below.append(gap < 0)
        add("trend", f"trend_{safe_name(t)}", f"{nm} against its 200-day average", gap, "%", s_.index[-1].date(), None,
            "above" if gap >= 0 else "below", f"12-month change {r12:+.1f}%; a falling market below its 200-day average is where crashes happen",
            "Yahoo Finance via stan-the-man-data")
    if below:
        comp["trend"] = 100 * sum(below) / len(below)

    # 6. Concentration and neglect (Burry): equal weight vs cap weight, small vs large, value vs growth
    for a, b, key, nm, text in (("RSP", "SPY", "eq_vs_cap", "S&P 500 equal weight against cap weight (RSP/SPY)",
                                 "How much of the market's gain comes from its largest stocks; Burry's 2019 passive-concentration argument"),
                                ("IWM", "SPY", "small_vs_large", "US small caps against the S&P 500 (IWM/SPY)", "Neglected small caps are where Burry looks for value"),
                                ("IWD", "IWF", "value_vs_growth", "US value against growth (IWD/IWF)", "Out-of-favour styles: where the 'ick' is")):
        sa, sb = px(a), px(b)
        if sa is None or sb is None:
            continue
        r = (sa / sb).dropna()
        r3 = (r.iloc[-1] / r[r.index <= r.index[-1] - pd.Timedelta(days=3 * 365)].iloc[-1] - 1) * 100
        r1 = (r.iloc[-1] / r[r.index <= r.index[-1] - pd.Timedelta(days=365)].iloc[-1] - 1) * 100
        pts = clip(-r3 / 20 * 100) if key == "eq_vs_cap" else None
        if pts is not None:
            comp["concentration"] = pts
        add("concentration", key, nm, r3, "% over 3 years", r.index[-1].date(), pct_rank(r, r.iloc[-1]),
            f"{r1:+.1f}% over 1 year; ratio at the {ordinal(pct_rank(r, r.iloc[-1]))} percentile of its history", text,
            "Yahoo Finance via stan-the-man-data", points=pts)
        hist = hist.join((r / r.iloc[0] * 100).resample("ME").last().rename(key), how="outer")

    # 7. Franc and growth signals
    eur = px("EURCHF=X", "close")
    if eur is not None:
        c1 = (eur.iloc[-1] / eur[eur.index <= eur.index[-1] - pd.Timedelta(days=365)].iloc[-1] - 1) * 100
        add("switzerland", "eurchf", "EUR/CHF", eur.iloc[-1], "CHF per EUR", eur.index[-1].date(), pct_rank(eur, eur.iloc[-1]),
            f"{c1:+.1f}% over 1 year", "Below 0.90 the SNB tends to lean against the franc; a strong franc cuts the foreign earnings of Swiss multinationals",
            "Yahoo Finance via stan-the-man-data")
    cu, au = px("HG=F", "close"), px("GC=F", "close")
    if cu is not None and au is not None:
        r = (cu / au).dropna()
        c1 = (r.iloc[-1] / r[r.index <= r.index[-1] - pd.Timedelta(days=365)].iloc[-1] - 1) * 100
        add("cycle", "copper_gold", "Copper/gold ratio", r.iloc[-1] * 1000, "x1000", r.index[-1].date(), pct_rank(r, r.iloc[-1]),
            f"{c1:+.1f}% over 1 year", "Falling = markets price weaker growth or more fear; it usually moves with long bond yields",
            "Yahoo Finance via stan-the-man-data (HG=F, GC=F)")

    # ---------- composite ----------
    fragility = float(np.mean(list(comp.values()))) if comp else np.nan
    if not math.isnan(cape_pct):
        if cape_pct >= 80 or (not math.isnan(ecy_now) and ecy_now < 1.5):
            val_state = "expensive"
        elif cape_pct <= 35 and (math.isnan(ecy_now) or ecy_now >= 4):
            val_state = "cheap"
        else:
            val_state = "fair"
    else:
        val_state = "unknown"
    frag_state = "unknown" if math.isnan(fragility) else "high" if fragility >= 50 else "medium" if fragility >= 30 else "low"
    table = {
        ("cheap", "low"): ("Green light", "Upper end of the equity band; buy what qualifies", 15),
        ("cheap", "medium"): ("Burry zone", "Fear with value: buy in tranches, only what you understand", 20),
        ("cheap", "high"): ("Burry zone", "Fear with value: buy in tranches, only what you understand; expect more volatility", 20),
        ("fair", "low"): ("Normal", "Target weights; buy what qualifies", 20),
        ("fair", "medium"): ("Normal, watchful", "Target weights; no adding beyond the plan", 25),
        ("fair", "high"): ("Caution", "Target weights; no adding beyond the plan; review sell triggers", 25),
        ("expensive", "low"): ("Munger zone", "Own only wonderful businesses at fair prices; no new marginal risk; keep the reserve full", 25),
        ("expensive", "medium"): ("Late cycle", "Lower half of the equity band; new buys only with a wide margin of safety", 30),
        ("expensive", "high"): ("Defense", "Lower end of the band; review sell triggers first; buy nothing marginal", 35),
    }
    label, action, mos = table.get((val_state, frag_state), ("Unknown", "Data missing", 25))
    hurdle_real = (max(exp_real, 0.0) if not math.isnan(exp_real) else 3.0) + CONCENTRATION_PREMIUM
    hurdle_nominal = hurdle_real + CH_INFLATION
    summary = dict(run_utc=now_utc(), valuation_state=val_state, cape_percentile=None if math.isnan(cape_pct) else round(cape_pct, 1),
                   excess_cape_yield=None if math.isnan(ecy_now) else round(ecy_now, 2),
                   expected_us_real_10y=None if math.isnan(exp_real) else round(exp_real, 2),
                   expected_band_low=None if math.isnan(band[0]) else round(band[0], 2), expected_band_high=None if math.isnan(band[1]) else round(band[1], 2),
                   recession_prob_12m=None if math.isnan(rec_prob) else round(rec_prob, 1),
                   fragility=None if math.isnan(fragility) else round(fragility, 1), fragility_state=frag_state,
                   **{f"frag_{k}": round(v, 1) for k, v in comp.items()},
                   regime=label, action=action, required_margin_of_safety=mos,
                   hurdle_real=round(hurdle_real, 2), hurdle_nominal_chf=round(hurdle_nominal, 2),
                   required_return_floor=REQUIRED_RETURN_FLOOR, concentration_premium=CONCENTRATION_PREMIUM, ch_inflation=CH_INFLATION)
    write_csv(pd.DataFrame(rows), DATA / "derived" / "regime.csv", index=False)
    write_csv(pd.DataFrame([summary]), DATA / "derived" / "regime_summary.csv", index=False)
    if len(hist):
        hist = hist[hist.index >= "1990-01-01"].round(3)
        hist.index.name = "date"
        write_csv(hist, DATA / "derived" / "regime_history.csv")
    log_status("regime", "summary", True, len(rows), f"{label}: valuation {val_state}, fragility {frag_state}")
    print(pd.DataFrame(rows)[["key", "value", "percentile", "fragility_points"]].to_string())
    print(summary)


if __name__ == "__main__":
    main()
