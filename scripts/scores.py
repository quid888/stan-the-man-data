"""Munger and Burry layers as numbers, for every holding, candidate and peer on the watchlist.

Three scores from 0 to 100 plus a contrarian 'ick' reading, built from Yahoo statements (annual), the latest
price and the market regime (scripts/regime.py):

  Munger quality   Is this a business that compounds capital? Return on invested capital (level and floor),
                   gross margin (level and stability), cash conversion, capital intensity, balance sheet, dilution.
  Burry value      What does the price ask for? Free-cash-flow yield after stock pay, EV/EBITDA, EV/(EBITDA - capex),
                   the growth the price implies against the growth the business can fund, tangible asset backing,
                   net buybacks.
  Forensic safety  Can the numbers be trusted, and can the company survive a bad year? Beneish M-score, Sloan
                   accruals, Piotroski F-score, Altman Z'' (non-manufacturing version), receivables against sales,
                   capex against depreciation, stock pay against free cash flow.
  Ick              How unloved is it? Fall from the 3-year high, analysts' average rating, price under its 200-day average.

Outputs: data/derived/scores.csv (one row per ticker), data/derived/scores_detail.csv (every metric with its points).
Ratios are computed in one currency: statement values are converted to the trading currency with the repo's FX rates.
Not for banks or insurers (the forensic models exclude financial firms).

Run: python scripts/scores.py
"""
import math
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from common import DATA, log_status, now_utc, safe_name, watchlist, write_csv  # noqa: E402

FXT = {"USD": "CHF=X", "EUR": "EURCHF=X", "GBP": "GBPCHF=X"}

# piecewise-linear scales: (metric value, points out of 10)
SCALES = {
    # Munger quality
    "roic_avg": [(4, 0), (8, 3), (12, 6), (16, 8), (22, 10)],
    "roic_min": [(0, 0), (6, 4), (10, 7), (15, 10)],
    "gross_margin": [(10, 0), (25, 4), (40, 7), (55, 10)],
    "gross_margin_sd": [(0.5, 10), (1.5, 8), (3, 5), (6, 0)],
    "fcf_conversion": [(0.3, 0), (0.6, 4), (0.85, 8), (1.0, 10)],
    "capex_to_sales": [(2, 10), (4, 8), (7, 5), (12, 0)],
    "net_debt_ebitda": [(0, 10), (1, 9), (2, 6), (3, 3), (4, 0)],
    "share_cagr": [(-2, 10), (0, 8), (1, 5), (3, 0)],
    # Burry value
    "fcf_yield": [(1, 0), (3, 3), (5, 6), (7, 8), (10, 10)],
    "ev_ebitda": [(6, 10), (8, 8), (11, 5), (15, 2), (20, 0)],
    "ev_ebitda_capex": [(8, 10), (11, 8), (15, 5), (20, 2), (28, 0)],
    "growth_cushion": [(-4, 0), (-2, 3), (0, 5), (2, 8), (4, 10)],
    "tangible_book_to_price": [(-0.2, 0), (0, 2), (0.3, 5), (0.6, 8), (1.0, 10)],
    "net_buyback_yield": [(-2, 0), (0, 5), (2, 8), (4, 10)],
    # Forensic safety
    "beneish_m": [(-3.0, 10), (-2.22, 7), (-1.78, 3), (-1.0, 0)],
    "sloan_accruals": [(-5, 10), (0, 8), (5, 4), (10, 0)],
    "piotroski_f": [(0, 0), (3, 3), (5, 6), (7, 9), (8, 10)],
    "altman_z2": [(0, 0), (1.1, 3), (2.6, 7), (4, 10)],
    "receivables_gap": [(0, 10), (5, 7), (10, 3), (20, 0)],
    "capex_to_dep": [(0.4, 0), (0.7, 5), (0.9, 10)],
    "sbc_to_fcf": [(0, 10), (5, 8), (15, 4), (30, 0)],
}
WEIGHTS = {
    "munger_quality": {"roic_avg": 25, "roic_min": 10, "gross_margin": 10, "gross_margin_sd": 10, "fcf_conversion": 15,
                       "capex_to_sales": 10, "net_debt_ebitda": 10, "share_cagr": 10},
    "burry_value": {"fcf_yield": 30, "ev_ebitda": 20, "ev_ebitda_capex": 15, "growth_cushion": 20,
                    "tangible_book_to_price": 10, "net_buyback_yield": 5},
    "forensic_safety": {"beneish_m": 20, "sloan_accruals": 15, "piotroski_f": 20, "altman_z2": 20,
                        "receivables_gap": 10, "capex_to_dep": 5, "sbc_to_fcf": 10},
}


def points(metric, x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    pts = SCALES[metric]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return float(np.interp(x, xs, ys))


def nz(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def load(ticker, name):
    p = DATA / "fundamentals" / safe_name(ticker) / f"{name}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, parse_dates=["period_end"]).set_index("period_end").sort_index()
    return df[df.notna().sum(axis=1) >= 8]


def g(df, names, i):
    """Value of the first available line item in row i (negative i counts from the end)."""
    if df.empty or abs(i) > len(df):
        return np.nan
    row = df.iloc[i]
    for n in names if isinstance(names, (list, tuple)) else [names]:
        if n in row.index and pd.notna(row[n]):
            return float(row[n])
    return np.nan


def price(ticker):
    p = DATA / "prices" / f"{safe_name(ticker)}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, index_col="date", parse_dates=True)["close"].dropna()


def chf_rate(cur, cache):
    if cur in (None, "", "CHF"):
        return 1.0
    if cur not in cache:
        s = price(FXT[cur]) if cur in FXT else None
        cache[cur] = float(s.iloc[-1]) if s is not None and len(s) else np.nan
    return cache[cur]


def safe_div(a, b):
    return a / b if nz(a) and nz(b) and b != 0 else np.nan


def score_ticker(row, regime, fxc):
    t = row.ticker
    inc, bal, cf = load(t, "income_annual"), load(t, "balance_annual"), load(t, "cashflow_annual")
    balq = load(t, "balance_quarterly")
    snap_p = DATA / "fundamentals" / safe_name(t) / "snapshot.csv"
    snap = pd.read_csv(snap_p).iloc[-1] if snap_p.exists() else pd.Series(dtype=object)
    px = price(t)
    if inc.empty or bal.empty or cf.empty or px is None:
        return None, []
    trade_cur = "GBP" if row.price_unit == "pence" else row.currency
    stmt_cur = snap.get("financialCurrency") if isinstance(snap.get("financialCurrency"), str) else trade_cur
    fx = safe_div(chf_rate(stmt_cur, fxc), chf_rate(trade_cur, fxc))   # statement currency -> trading currency
    last_px = float(px.iloc[-1]) / (100 if row.price_unit == "pence" else 1)
    # all share classes priced at this line's price (Schindler, Villeroy & Boch list two classes)
    shares = np.nanmax([float(snap.get("sharesOutstanding")) if nz(snap.get("sharesOutstanding")) else np.nan,
                        g(bal, ["Ordinary Shares Number", "Share Issued"], -1)])
    mcap = last_px * shares
    flags = []
    n = min(len(inc), 4)
    m = {}

    # ---- Munger quality ----
    def ic(i):
        return g(bal, ["Total Debt"], i) + g(bal, ["Total Equity Gross Minority Interest", "Stockholders Equity"], i) - \
            np.nan_to_num(g(bal, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], i))
    roics, gms = [], []
    for i in range(-n, 0):
        ebit = g(inc, ["EBIT", "Operating Income"], i)
        tax = g(inc, ["Tax Rate For Calcs"], i)
        tax = 0.21 if not nz(tax) else min(max(tax, 0.0), 0.4)
        cap = np.nanmean([ic(i), ic(i - 1)]) if abs(i - 1) <= len(bal) else ic(i)
        if nz(ebit) and nz(cap) and cap > 0:
            roics.append(ebit * (1 - tax) / cap * 100)
        rev, gp = g(inc, ["Total Revenue", "Operating Revenue"], i), g(inc, ["Gross Profit"], i)
        if not nz(gp):
            gp = rev - g(inc, ["Cost Of Revenue", "Reconciled Cost Of Revenue"], i)
        if nz(rev) and nz(gp) and rev > 0:
            gms.append(gp / rev * 100)
    m["roic_avg"] = float(np.mean(roics)) if roics else np.nan
    m["roic_min"] = float(np.min(roics)) if roics else np.nan
    m["gross_margin"] = float(np.mean(gms)) if gms else np.nan
    m["gross_margin_sd"] = float(np.std(gms, ddof=1)) if len(gms) >= 3 else np.nan
    k = min(len(cf), 4)
    fcfs = [g(cf, ["Free Cash Flow"], i) for i in range(-k, 0)]
    nis = [g(inc, ["Net Income Common Stockholders", "Net Income"], i) for i in range(-min(n, k), 0)]
    m["fcf_conversion"] = safe_div(np.nansum(fcfs[-len(nis):]), np.nansum(nis)) if nis else np.nan
    revs = [g(inc, ["Total Revenue", "Operating Revenue"], i) for i in range(-k, 0)]
    capexs = [abs(g(cf, ["Capital Expenditure", "Capital Expenditure Reported"], i)) for i in range(-k, 0)]
    m["capex_to_sales"] = safe_div(np.nansum(capexs), np.nansum(revs[-len(capexs):])) * 100
    debt, cash = g(bal, ["Total Debt"], -1), g(bal, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], -1)
    if not balq.empty and balq.index[-1] > bal.index[-1]:
        debt = g(balq, ["Total Debt"], -1) if nz(g(balq, ["Total Debt"], -1)) else debt
        cash = g(balq, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], -1) if nz(
            g(balq, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], -1)) else cash
    # Yahoo's statement debt can miss bonds (Swisscom 2025: 3.7bn against 16bn in the snapshot): take the larger debt
    # and the smaller cash, and flag a gap above 30% or an implied interest rate above 8%
    snap_debt = float(snap.get("totalDebt")) * 1.0 if nz(snap.get("totalDebt")) else np.nan
    snap_cash = float(snap.get("totalCash")) * 1.0 if nz(snap.get("totalCash")) else np.nan
    if nz(snap_debt) and nz(debt) and max(snap_debt, debt) > 0 and abs(snap_debt - debt) / max(snap_debt, debt) > 0.3:
        flags.append(f"debt sources differ: statement {debt / 1e9:.2f}bn, snapshot {snap_debt / 1e9:.2f}bn")
    debt = np.nanmax([debt, snap_debt]) if nz(debt) or nz(snap_debt) else np.nan
    cash = np.nanmin([cash, snap_cash]) if nz(cash) or nz(snap_cash) else np.nan
    int_exp = abs(g(inc, ["Interest Expense", "Interest Expense Non Operating"], -1))
    if nz(int_exp) and nz(debt) and debt > 0 and int_exp / debt > 0.08:
        flags.append(f"interest expense is {int_exp / debt * 100:.0f}% of debt: debt may be understated")
    minority = np.nan_to_num(g(bal, ["Minority Interest"], -1))
    ebitda = g(inc, ["EBITDA", "Normalized EBITDA"], -1)
    net_debt = np.nan_to_num(debt) - np.nan_to_num(cash)
    m["net_debt_ebitda"] = safe_div(net_debt, ebitda)
    if nz(m["net_debt_ebitda"]) and net_debt < 0:
        m["net_debt_ebitda"] = 0.0
    sh = [g(inc, ["Diluted Average Shares", "Basic Average Shares"], i) for i in range(-n, 0)]
    sh = [x for x in sh if nz(x) and x > 0]
    m["share_cagr"] = ((sh[-1] / sh[0]) ** (1 / (len(sh) - 1)) - 1) * 100 if len(sh) >= 2 else np.nan

    # ---- Burry value ----
    sbc = [g(cf, ["Stock Based Compensation"], i) for i in range(-min(k, 3), 0)]
    fcf3 = [x for x in fcfs[-3:] if nz(x)]
    # minority holders own part of the cash flow (Deutsche Telekom and T-Mobile US): keep the shareholders' share
    ni_c, ni_all = g(inc, ["Net Income Common Stockholders", "Net Income"], -1), g(inc, ["Net Income Including Noncontrolling Interests"], -1)
    own = min(1.0, max(0.3, ni_c / ni_all)) if nz(ni_c) and nz(ni_all) and ni_all > 0 and ni_c > 0 else 1.0
    if own < 0.95:
        flags.append(f"minority interests take {100 - own * 100:.0f}% of profit")
    # IFRS 16 moves lease payments out of operating cash flow; Burry adds them back as a cost. Next year's lease principal
    # is roughly the current lease liability, so it is deducted (telecoms and retailers otherwise look far too cheap)
    lease_cur = np.nan_to_num(g(bal, ["Current Capital Lease Obligation"], -1))
    if lease_cur > 0:
        flags.append(f"free cash flow reduced by {lease_cur / 1e9:.2f}bn of lease payments (estimate)")
    norm_fcf = (np.mean(fcf3) - lease_cur - np.nan_to_num(np.nanmean(sbc) if any(nz(x) for x in sbc) else 0.0)) * fx * own if fcf3 else np.nan
    ev = mcap + (net_debt + minority) * fx
    m["fcf_yield"] = safe_div(norm_fcf, mcap) * 100
    m["ev_ebitda"] = safe_div(ev, ebitda * fx)
    capex_last = abs(g(cf, ["Capital Expenditure", "Capital Expenditure Reported"], -1))
    m["ev_ebitda_capex"] = safe_div(ev, (ebitda - capex_last) * fx) if nz(ebitda) and nz(capex_last) and ebitda > capex_last else np.nan
    tbv = g(bal, ["Tangible Book Value"], -1)
    m["tangible_book_to_price"] = safe_div(tbv * fx, mcap)
    buyback = abs(np.nan_to_num(g(cf, ["Repurchase Of Capital Stock", "Common Stock Payments"], -1)))
    issued = max(0.0, np.nan_to_num(g(cf, ["Common Stock Issuance"], -1)))
    m["net_buyback_yield"] = safe_div((buyback - issued) * fx, mcap) * 100
    # growth the business can fund (Munger) against the growth the price implies (reverse discounted cash flow)
    ebit = g(inc, ["EBIT", "Operating Income"], -1)
    tax = g(inc, ["Tax Rate For Calcs"], -1)
    nopat = ebit * (1 - (0.21 if not nz(tax) else min(max(tax, 0), 0.4)))
    # growth the business funds itself = return on capital x share of profit reinvested (net capex, acquisitions,
    # working capital), averaged over up to three years
    reinv = nop = 0.0
    for i in range(-min(3, len(cf), len(inc)), 0):
        e_, t_ = g(inc, ["EBIT", "Operating Income"], i), g(inc, ["Tax Rate For Calcs"], i)
        if not nz(e_):
            continue
        nop += e_ * (1 - (0.21 if not nz(t_) else min(max(t_, 0), 0.4)))
        reinv += abs(np.nan_to_num(g(cf, ["Capital Expenditure", "Capital Expenditure Reported"], i))) \
            - abs(np.nan_to_num(g(cf, ["Depreciation And Amortization", "Depreciation"], i))) \
            + abs(np.nan_to_num(g(cf, ["Purchase Of Business"], i))) - abs(np.nan_to_num(g(cf, ["Sale Of Business"], i))) \
            - np.nan_to_num(g(cf, ["Change In Working Capital"], i))
    reinvest_rate = min(1.0, max(0.0, reinv / nop)) if nop > 0 else np.nan
    g_fund = min(8.0, max(0.0, m["roic_avg"] * reinvest_rate)) if nz(m["roic_avg"]) and nz(reinvest_rate) else np.nan
    est = DATA / "fundamentals" / safe_name(t) / "estimates_eps.csv"
    g_cons = np.nan
    if est.exists():
        e = pd.read_csv(est).set_index("period")
        if "+1y" in e.index and "0y" in e.index and nz(e.loc["0y", "avg"]) and e.loc["0y", "avg"] > 0:
            g_cons = min(8.0, max(-2.0, (e.loc["+1y", "avg"] / e.loc["0y", "avg"] - 1) * 100))
    # growth estimate: the median of three imperfect witnesses (revenue growth over the years on file, analysts' next-year
    # EPS growth, return on capital x reinvestment), capped at 6% a year for ten years. Large acquisitions inflate all
    # three, so then only the analysts' figure counts, capped at 4%.
    revs_all = [g(inc, ["Total Revenue", "Operating Revenue"], i) for i in range(-n, 0)]
    revs_all = [x for x in revs_all if nz(x) and x > 0]
    g_rev = min(10.0, max(-2.0, ((revs_all[-1] / revs_all[0]) ** (1 / (len(revs_all) - 1)) - 1) * 100)) if len(revs_all) >= 3 else np.nan
    acq = sum(abs(np.nan_to_num(g(cf, ["Purchase Of Business"], i))) for i in range(-min(3, len(cf)), 0)) * fx
    big_deals = nz(acq) and mcap > 0 and acq / mcap > 0.15
    if big_deals:
        flags.append(f"acquisitions of {acq / 1e9:.1f}bn in three years: growth history inflated")
        g_est = min(g_cons, 4.0) if nz(g_cons) else 3.0
    else:
        wit = [x for x in (g_rev, g_cons, g_fund) if nz(x)]
        g_est = float(np.median(wit)) if wit else np.nan
    g_est = min(6.0, max(0.0, g_est)) if nz(g_est) else np.nan
    r_req = max(regime.get("hurdle_nominal_chf", 7.0), regime.get("required_return_floor", 7.0))

    def dcf(gr):   # 10 years at gr, then 2.5% for ever, discounted at r_req
        r, gt = r_req / 100, min(0.025, r_req / 100 - 0.02)
        f, v = norm_fcf, 0.0
        for yr in range(1, 11):
            f *= 1 + gr / 100
            v += f / (1 + r) ** yr
        return v + f * (1 + gt) / (r - gt) / (1 + r) ** 10
    implied, value = np.nan, np.nan
    if nz(norm_fcf) and norm_fcf > 0:
        lo, hi = -30.0, 40.0
        if dcf(lo) < mcap < dcf(hi):
            for _ in range(60):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if dcf(mid) < mcap else (lo, mid)
            implied = (lo + hi) / 2
        value = dcf(g_est) if nz(g_est) else np.nan
    m["growth_cushion"] = g_est - implied if nz(g_est) and nz(implied) else np.nan
    mos = (1 - mcap / value) * 100 if nz(value) and value > 0 else np.nan
    exp_irr = m["fcf_yield"] + g_est if nz(m["fcf_yield"]) and nz(g_est) else np.nan

    # ---- Forensic safety ----
    def two(names, src):
        return g(src, names, -1), g(src, names, -2)
    sales, sales0 = two(["Total Revenue", "Operating Revenue"], inc)
    cogs, cogs0 = two(["Cost Of Revenue", "Reconciled Cost Of Revenue"], inc)
    ar, ar0 = two(["Accounts Receivable", "Receivables", "Gross Accounts Receivable"], bal)
    ca, ca0 = two(["Current Assets"], bal)
    ppe, ppe0 = two(["Net PPE"], bal)
    sec, sec0 = two(["Other Short Term Investments"], bal)
    ta, ta0 = two(["Total Assets"], bal)
    dep, dep0 = two(["Reconciled Depreciation", "Depreciation And Amortization"], inc)
    if not nz(dep):
        dep, dep0 = two(["Depreciation And Amortization", "Depreciation"], cf)
    sga, sga0 = two(["Selling General And Administration"], inc)
    cl, cl0 = two(["Current Liabilities"], bal)
    ltd, ltd0 = two(["Long Term Debt And Capital Lease Obligation", "Long Term Debt"], bal)
    nic = g(inc, ["Net Income From Continuing Operation Net Minority Interest", "Net Income Continuous Operations", "Net Income"], -1)
    cfo, cfo0 = two(["Operating Cash Flow"], cf)
    used = 0

    def idx(v, default):
        nonlocal used
        if nz(v) and not math.isinf(v):
            used += 1
            return v
        return default
    dsri = idx(safe_div(safe_div(ar, sales), safe_div(ar0, sales0)), 1.0)
    gmi = idx(safe_div(safe_div(sales0 - cogs0, sales0), safe_div(sales - cogs, sales)), 1.0)
    aqi = idx(safe_div(1 - safe_div(ca + ppe + np.nan_to_num(sec), ta), 1 - safe_div(ca0 + ppe0 + np.nan_to_num(sec0), ta0)), 1.0)
    sgi = idx(safe_div(sales, sales0), 1.0)
    depi = idx(safe_div(safe_div(dep0, dep0 + ppe0), safe_div(dep, dep + ppe)), 1.0)
    sgai = idx(safe_div(safe_div(sga, sales), safe_div(sga0, sales0)), 1.0)
    lvgi = idx(safe_div(safe_div(cl + np.nan_to_num(ltd), ta), safe_div(cl0 + np.nan_to_num(ltd0), ta0)), 1.0)
    tata = idx(safe_div(nic - cfo, ta), 0.0)
    m["beneish_m"] = -4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi \
        if used >= 6 else np.nan
    ni, ni0 = two(["Net Income Common Stockholders", "Net Income"], inc)
    m["sloan_accruals"] = safe_div(ni - cfo, np.nanmean([ta, ta0])) * 100
    # Piotroski: nine binary signals, t against t-1
    roa, roa0 = safe_div(ni, ta0), safe_div(ni0, g(bal, ["Total Assets"], -3))
    lev, lev0 = safe_div(ltd, ta), safe_div(ltd0, ta0)
    cr, cr0 = safe_div(ca, cl), safe_div(ca0, cl0)
    gm, gm0 = safe_div(sales - cogs, sales), safe_div(sales0 - cogs0, sales0)
    at, at0 = safe_div(sales, ta0), safe_div(sales0, g(bal, ["Total Assets"], -3))
    shs, shs0 = g(bal, ["Ordinary Shares Number", "Share Issued"], -1), g(bal, ["Ordinary Shares Number", "Share Issued"], -2)
    sig = [nz(roa) and roa > 0, nz(cfo) and cfo > 0, nz(roa) and nz(roa0) and roa > roa0, nz(cfo) and nz(ni) and cfo > ni,
           nz(lev) and nz(lev0) and lev <= lev0, nz(cr) and nz(cr0) and cr > cr0, nz(shs) and nz(shs0) and shs <= shs0 * 1.005,
           nz(gm) and nz(gm0) and gm > gm0, nz(at) and nz(at0) and at > at0]
    m["piotroski_f"] = float(sum(bool(s) for s in sig))
    wc = ca - cl
    re_ = g(bal, ["Retained Earnings"], -1)
    eq = g(bal, ["Total Equity Gross Minority Interest", "Stockholders Equity"], -1)
    tl = g(bal, ["Total Liabilities Net Minority Interest"], -1)
    m["altman_z2"] = 6.56 * safe_div(wc, ta) + 3.26 * safe_div(re_, ta) + 6.72 * safe_div(ebit, ta) + 1.05 * safe_div(eq, tl) \
        if all(nz(x) for x in (wc, re_, ebit, eq, tl, ta)) else np.nan
    m["receivables_gap"] = (safe_div(ar, ar0) - safe_div(sales, sales0)) * 100 if nz(ar0) and ar0 > 0 else np.nan
    m["capex_to_dep"] = safe_div(capex_last, dep if nz(dep) else g(cf, ["Depreciation And Amortization"], -1))
    m["sbc_to_fcf"] = safe_div(abs(np.nan_to_num(g(cf, ["Stock Based Compensation"], -1))), g(cf, ["Free Cash Flow"], -1)) * 100
    if nz(m["sbc_to_fcf"]) and m["sbc_to_fcf"] < 0:
        m["sbc_to_fcf"] = 100.0   # stock pay with negative free cash flow

    # ---- Ick (contrarian) ----
    p3 = px[px.index >= px.index[-1] - pd.Timedelta(days=3 * 365)]
    dd3 = (1 - px.iloc[-1] / p3.max()) * 100
    ma200 = px.rolling(200).mean().iloc[-1] if len(px) >= 200 else np.nan
    rec = snap.get("recommendationMean")
    parts = [(0.4, min(100.0, dd3 / 40 * 100))]
    if nz(ma200):
        parts.append((0.3, 100.0 if px.iloc[-1] < ma200 else 0.0))
    if nz(rec):
        parts.append((0.3, min(100.0, max(0.0, (float(rec) - 2.0) / 1.5 * 100))))
    ick = sum(w * v for w, v in parts) / sum(w for w, _ in parts)

    # ---- scores ----
    detail, out = [], {}
    for layer, wts in WEIGHTS.items():
        num = den = 0.0
        for met, w in wts.items():
            p = points(met, m.get(met))
            detail.append(dict(ticker=t, layer=layer, metric=met, value=None if not nz(m.get(met)) else round(float(m[met]), 4),
                               points=None if p is None else round(p, 2), weight=w))
            if p is not None:
                num += p * w
                den += w
        out[layer] = round(num / den * 10, 1) if den >= 50 else np.nan
        out[f"{layer}_coverage"] = round(den / sum(wts.values()) * 100)
    q, v, f = out["munger_quality"], out["burry_value"], out["forensic_safety"]
    req = regime.get("required_margin_of_safety", 25)
    if nz(f) and f < 50:
        label = "Too hard: red flags, forensic review first"
    elif nz(q) and q >= 70 and nz(v) and v >= 55:
        label = "Munger-Burry overlap: wonderful business at a fair price"
    elif nz(q) and q >= 70:
        label = "Wonderful business, wait for the price"
    elif nz(v) and v >= 65 and nz(f) and f >= 65:
        label = "Burry candidate: cheap and clean; needs a catalyst, small size"
    else:
        label = "Pass: neither great nor cheap enough"
    res = dict(ticker=t, name=row.name, role=row.role, group=row.group, as_of=str(px.index[-1].date()),
               fiscal_year=str(inc.index[-1].date()), trade_currency=trade_cur, statement_currency=stmt_cur,
               price=round(last_px, 4), market_cap=round(mcap), enterprise_value=round(ev) if nz(ev) else None,
               **{k: (round(float(x), 4) if nz(x) else None) for k, x in m.items()},
               reinvestment_rate=round(reinvest_rate * 100, 1) if nz(reinvest_rate) else None,
               growth_revenue=round(g_rev, 2) if nz(g_rev) else None,
               growth_fundable=round(g_fund, 2) if nz(g_fund) else None, growth_consensus=round(g_cons, 2) if nz(g_cons) else None,
               growth_estimate=round(g_est, 2) if nz(g_est) else None, required_return=round(r_req, 2),
               implied_growth=round(implied, 2) if nz(implied) else None,
               expected_return=round(exp_irr, 2) if nz(exp_irr) else None,
               hurdle=regime.get("hurdle_nominal_chf"),
               opportunity_spread=round(exp_irr - regime.get("hurdle_nominal_chf", 7.0), 2) if nz(exp_irr) else None,
               intrinsic_value_per_share=round(value / shares, 4) if nz(value) and shares else None,
               margin_of_safety=round(mos, 1) if nz(mos) else None, required_margin_of_safety=req,
               meets_margin_of_safety=bool(nz(mos) and mos >= req),
               ick=round(ick, 1), drawdown_3y=round(dd3, 1), below_200d=bool(nz(ma200) and px.iloc[-1] < ma200),
               analyst_rating=round(float(rec), 2) if nz(rec) else None,
               munger_quality=q, burry_value=v, forensic_safety=f,
               quality_coverage=out["munger_quality_coverage"], value_coverage=out["burry_value_coverage"],
               forensic_coverage=out["forensic_safety_coverage"], beneish_inputs=used, ownership_share=round(own, 3),
               data_flags="; ".join(flags), label=label, run_utc=now_utc())
    return res, detail


def main():
    wl = watchlist()
    rp = DATA / "derived" / "regime_summary.csv"
    regime = pd.read_csv(rp).iloc[-1].to_dict() if rp.exists() else {}
    fxc, rows, details = {}, [], []
    for row in wl[wl["role"].isin(["holding", "candidate", "peer"])].itertuples():
        try:
            res, det = score_ticker(row, regime, fxc)
            if res:
                rows.append(res)
                details += det
                log_status("scores", row.ticker, True, 1, res["label"])
            else:
                log_status("scores", row.ticker, False, 0, "missing statements or prices")
        except Exception as e:  # noqa: BLE001
            log_status("scores", row.ticker, False, 0, e)
            print(row.ticker, "FAILED", repr(e))
    if rows:
        write_csv(pd.DataFrame(rows), DATA / "derived" / "scores.csv", index=False)
        write_csv(pd.DataFrame(details), DATA / "derived" / "scores_detail.csv", index=False)
        print(pd.DataFrame(rows)[["ticker", "munger_quality", "burry_value", "forensic_safety", "ick", "margin_of_safety", "label"]].to_string())


if __name__ == "__main__":
    main()
