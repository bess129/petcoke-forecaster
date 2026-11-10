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
METAL_KEYWORDS = {
    "Cobalt": r"cobalt|congo|drc|katanga",
    "Antimony": r"antimony",
    "Gallium/Germanium (HS 811292)": r"gallium|germanium|indium|gan",
    "Molybdenum": r"molybdenum|moly",
    "Lithium": r"lithium|spodumene",
    "Rare Earths": r"rare[- ]earths?|neodymium|ndpr|praseodymium|dysprosium|terbium|lynas|mp materials",
    "Tin": r"tin|cassiterite|bisie|wa state",
}
ALL_METALS = "|".join(METAL_KEYWORDS.values())


def signals_for(signals_df, commodity):
    """Headlines relevant to one commodity.

    Pet Coke -> all Pet Coke headlines.
    A metal  -> Minor Metals headlines that name THIS metal, plus general ones
                that name no specific metal (e.g. 'China tightens critical
                minerals exports'). Antimony news no longer moves cobalt.
    Adds an 'applies_to' column: 'This metal' or 'All metals (general)'."""
    if signals_df is None or signals_df.empty:
        return pd.DataFrame(columns=getattr(signals_df, "columns", []))
    s = signals_df.copy()
    if commodity.startswith("Pet Coke") or commodity not in METAL_KEYWORDS:
        out = s[s["commodity"] == "Pet Coke"].copy()
        out["applies_to"] = "Pet Coke"
        return out
    s = s[s["commodity"] == "Minor Metals"]
    title = s["title"].fillna("").str.lower()
    mine = title.str.contains(rf"\b(?:{METAL_KEYWORDS[commodity]})\b", regex=True)
    any_metal = title.str.contains(rf"\b(?:{ALL_METALS})\b", regex=True)
    out = s[mine | ~any_metal].copy()
    out["applies_to"] = mine[mine | ~any_metal].map({True: "This metal",
                                                    False: "All metals (general)"})
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
                       commodity="Pet Coke (fuel-grade)", today=None):
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
GENERAL_WEIGHT = 0.4   # weight of headlines that name no specific metal


def supply_outlook(signals_df, histories, today=None, days=OUTLOOK_DAYS):
    """
    One row per commodity, ranked by supply-shortage pressure.

    histories: {commodity: history DataFrame with ds, y}

    supply_pressure = urgency-weighted average price_direction of SUPPLY-type
                      signals (outages, export rules, host-metal cuts, refinery
                      turnarounds, logistics) in the last `days` days, -1..+1.
    risk_score      = supply_pressure x confidence, where confidence grows with
                      the number of supply signals (full at 5). Used for ranking.
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
        if not sup.empty:
            # metal-specific news counts fully, general critical-minerals news 40%
            relevance = np.where(sup["applies_to"] == "All metals (general)", GENERAL_WEIGHT, 1.0)
            w = sup["urgency_score"].astype(float).values * relevance
            pressure = float((w * sup["price_direction"].astype(float).values).sum() / w.sum())
            top = (sup.assign(k=w * sup["price_direction"].abs().values, specific=relevance == 1.0)
                      .sort_values(["specific", "k"], ascending=False).iloc[0])
            driver = f"[{int(top['urgency_score'])}] {top['title']}"
            n_specific = int((relevance == 1.0).sum())
            effective_n = float(relevance.sum())
        else:
            pressure, driver, n_specific, effective_n = 0.0, "", 0, 0.0
        n_sup = len(sup)
        confidence = min(1.0, effective_n / 5)
        risk = pressure * confidence

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
                     "top_driver": driver})
    out = pd.DataFrame(rows)
    return out.sort_values(["risk_score", "price_3m"], ascending=False).reset_index(drop=True)
