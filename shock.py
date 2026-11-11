"""
shock.py - Weak-signal "Regime Shift" overlay (Step 6 of the sprint brief).

Takes the baseline forecast and bends it up or down when Claude has found
recent high-urgency weak signals (or when the user sets a manual shock).
Transparent on purpose: no extra model, just a weighted score and a ramp.
"""

import numpy as np
import pandas as pd

LOOKBACK_DAYS = 7      # only signals published in the last N days count
MIN_URGENCY = 4        # only urgency 4-5 signals count
THRESHOLD = 0.2        # net score must exceed this to trigger a regime shift
MAX_SIGNAL_SHIFT = 0.15  # strongest possible signal moves price 15% over 30 days
RAMP_DAYS = 30


# Which headlines belong to which metal (matched on whole words in the title).
# "names": the metal itself - always counts.
# "hints": places/companies strongly linked to the metal - only count when the
#          headline does not name a DIFFERENT metal (e.g. "Tin prices surge as
#          Congo..." is tin news, not cobalt news, even though it mentions Congo).
METALS = {
    "Cobalt":      {"names": r"cobalt",                             "hints": r"congo|drc|katanga"},
    "Antimony":    {"names": r"antimony",                           "hints": r"stibnite"},
    "Gallium/Germanium (HS 811292)": {"names": r"gallium|germanium|indium", "hints": r"gan"},
    "Molybdenum":  {"names": r"molybdenum",                         "hints": r"moly"},
    "Lithium":     {"names": r"lithium",                            "hints": r"spodumene"},
    "Rare Earths": {"names": r"rare[- ]earths?|neodymium|ndpr|praseodymium|dysprosium|terbium|yttrium",
                    "hints": r"lynas|mp materials"},
    "Tin":         {"names": r"tin|cassiterite",                    "hints": r"bisie|wa state"},
}
METAL_KEYWORDS = {m: f"{v['names']}|{v['hints']}" for m, v in METALS.items()}  # kept for reference
ALL_NAMES = "|".join(v["names"] for v in METALS.values())
ALL_TERMS = "|".join(METAL_KEYWORDS.values())


def _has(title, pattern):
    return title.str.contains(rf"\b(?:{pattern})\b", regex=True)


def signals_for(signals_df, commodity):
    """Headlines relevant to one commodity.

    Pet Coke -> all Pet Coke headlines.
    A metal  -> Minor Metals headlines about THIS metal, plus general ones that
                name no specific metal (e.g. 'China tightens critical minerals
                exports'). Antimony news no longer moves cobalt.
    Adds an 'applies_to' column: 'This metal' or 'All metals (general)'."""
    if signals_df is None or signals_df.empty:
        return pd.DataFrame(columns=getattr(signals_df, "columns", []))
    s = signals_df.copy()
    if "Pet Coke" in commodity or commodity not in METALS:
        out = s[s["commodity"] == "Pet Coke"].copy()
        out["applies_to"] = "Pet Coke"
        return out
    s = s[s["commodity"] == "Minor Metals"]
    title = s["title"].fillna("").str.lower()
    cfg = METALS[commodity]
    other_names = "|".join(v["names"] for m, v in METALS.items() if m != commodity)
    names_me = _has(title, cfg["names"])
    hints_me = _has(title, cfg["hints"]) & ~_has(title, other_names)
    mine = names_me | hints_me
    general = ~_has(title, ALL_TERMS)
    keep = mine | general
    out = s[keep].copy()
    out["applies_to"] = np.where(mine[keep], "This metal", "All metals (general)")
    return out


def signal_score(signals_df, commodity, today=None):
    """Urgency-weighted average price_direction of recent high-urgency signals
    relevant to `commodity`. Returns (net_score between -1 and +1, signals used)."""
    if signals_df is None or signals_df.empty:
        return 0.0, pd.DataFrame()
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    s = signals_for(signals_df, commodity)
    s["published_date"] = pd.to_datetime(s["published_date"], errors="coerce")
    recent = s[(s["urgency_score"] >= MIN_URGENCY)
               & (s["published_date"] >= today - pd.Timedelta(days=LOOKBACK_DAYS))]
    if recent.empty:
        return 0.0, recent
    w = recent["urgency_score"].astype(float)
    net = float((w * recent["price_direction"].astype(float)).sum() / w.sum())
    return net, recent


def apply_signal_shock(forecast_df, signals_df, manual_shock_pct=0.0,
                       commodity="Green Pet Coke (GPC)", today=None):
    """
    Returns (adjusted_df, info)

    adjusted_df keeps the baseline (yhat, yhat_lower, yhat_upper) and adds
    yhat_signal_adjusted, yhat_lower_adj, yhat_upper_adj.

    manual_shock_pct: e.g. 0.10 for +10% (from the dashboard slider).
    """
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    net, used = signal_score(signals_df, commodity, today)

    signal_shift = net * MAX_SIGNAL_SHIFT if abs(net) > THRESHOLD else 0.0
    total_shift = float(np.clip(signal_shift + manual_shock_pct, -0.30, 0.30))

    df = forecast_df.copy()
    # Ramp: 0 up to today, rising linearly to 1 at today + 30 days.
    days_ahead = (pd.to_datetime(df["ds"]) - today).dt.days.clip(lower=0)
    ramp = (days_ahead / RAMP_DAYS).clip(upper=1.0).values

    factor = 1 + total_shift * ramp
    width = (df["yhat_upper"] - df["yhat_lower"]).values
    widen = abs(total_shift) * ramp * 0.5 * width   # more uncertainty in a shock

    df["yhat_signal_adjusted"] = df["yhat"] * factor
    df["yhat_upper_adj"] = df["yhat_upper"] * factor + widen
    df["yhat_lower_adj"] = df["yhat_lower"] * factor - widen

    info = {
        "net_score": net,
        "n_signals": len(used),
        "signal_shift_pct": signal_shift,
        "manual_shift_pct": manual_shock_pct,
        "total_shift_pct": total_shift,
        "regime": "High Alert - Regime Shift" if abs(total_shift) >= 0.03 else "Normal",
        "signals_used": used,
    }
    return df, info


# --------------------------------------------------------------------------
# Supply Risk Outlook: which commodities face upside price risk from shortages?
# --------------------------------------------------------------------------
SUPPLY_CATEGORIES = {"Supply Outage", "Export Policy", "Host Metal Cut",
                     "Refinery Turnaround", "Logistics"}
OUTLOOK_DAYS = 30
GENERAL_WEIGHT = 0.25          # max share of risk that general headlines can add
SPECIFIC_FULL_CONFIDENCE = 5   # metal-specific supply headlines needed for full confidence


def supply_outlook(signals_df, histories, today=None, days=OUTLOOK_DAYS):
    """
    One row per commodity, ranked by supply-shortage pressure.

    histories: {commodity: history DataFrame with ds, y}

    supply_pressure = urgency-weighted average price_direction of SUPPLY-type
                      signals (outages, export rules, host-metal cuts, refinery
                      turnarounds, logistics) in the last `days` days, -1..+1.
    risk_score      = specific_pressure x min(1, specific headlines / 5)
                      + 0.25 x general_pressure x (general confidence)
                          x (1 - specific confidence)
                      Only headlines naming the metal can push it to "Upside risk";
                      general critical-minerals news adds at most ~0.06. Used for ranking.
    """
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    rows = []
    for commodity, hist in histories.items():
        s = signals_for(signals_df, commodity) if signals_df is not None else pd.DataFrame()
        if not s.empty:
            s = s.copy()
            s["published_date"] = pd.to_datetime(s["published_date"], errors="coerce")
            s = s[s["published_date"] >= today - pd.Timedelta(days=days)]
        sup = s[s["signal_category"].isin(SUPPLY_CATEGORIES)] if not s.empty else s
        # Split metal-specific vs general (critical-minerals) supply headlines.
        # Only specific news can drive a metal into "Upside risk"; general news
        # adds a small background score (at most ~0.06).
        def _pressure(df):
            if df.empty:
                return 0.0
            w = df["urgency_score"].astype(float)
            return float((w * df["price_direction"].astype(float)).sum() / w.sum())

        if not sup.empty:
            is_general = sup["applies_to"] == "All metals (general)"
            spec, gen = sup[~is_general], sup[is_general]
        else:
            spec = gen = sup
        p_spec, p_gen = _pressure(spec), _pressure(gen)
        n_specific, n_general = len(spec), len(gen)
        conf_spec = min(1.0, n_specific / SPECIFIC_FULL_CONFIDENCE)
        conf_gen = min(1.0, n_general / 5)
        risk = p_spec * conf_spec + GENERAL_WEIGHT * p_gen * conf_gen * (1 - conf_spec)
        risk = float(np.clip(risk, -1, 1))
        pressure = p_spec if n_specific else p_gen
        pick = spec if n_specific else gen
        if not pick.empty:
            k = pick["urgency_score"].astype(float) * pick["price_direction"].abs()
            top = pick.loc[k.idxmax()]
            driver = f"[{int(top['urgency_score'])}] {top['title']}"
            driver_link = top.get("link", "")
        else:
            driver, driver_link = "", ""
        n_sup = len(sup)
        y = hist["y"].astype(float).values
        mom3 = (y[-1] / y[-4] - 1) if len(y) >= 4 else np.nan
        mom12 = (y[-1] / y[-13] - 1) if len(y) >= 13 else np.nan

        if risk > 0.10:
            label = "Upside risk (supply)"
        elif risk < -0.10:
            label = "Downside risk"
        else:
            label = "Neutral"
        rows.append({"commodity": commodity, "outlook": label, "risk_score": round(risk, 2),
                     "supply_pressure": round(pressure, 2), "supply_signals": n_sup,
                     "specific_signals": n_specific,
                     "all_signals": len(s), "price_3m": mom3, "price_12m": mom12,
                     "top_driver": driver, "top_driver_link": driver_link})
    out = pd.DataFrame(rows)
    return out.sort_values(["risk_score", "price_3m"], ascending=False).reset_index(drop=True)
