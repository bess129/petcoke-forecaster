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


def signal_score(signals_df, commodity_group, today=None):
    """Urgency-weighted average price_direction of recent high-urgency signals.
    Returns (net_score between -1 and +1, the signals used)."""
    if signals_df is None or signals_df.empty:
        return 0.0, pd.DataFrame()
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    s = signals_df.copy()
    s["published_date"] = pd.to_datetime(s["published_date"], errors="coerce")
    recent = s[(s["commodity"] == commodity_group)
               & (s["urgency_score"] >= MIN_URGENCY)
               & (s["published_date"] >= today - pd.Timedelta(days=LOOKBACK_DAYS))]
    if recent.empty:
        return 0.0, recent
    w = recent["urgency_score"].astype(float)
    net = float((w * recent["price_direction"].astype(float)).sum() / w.sum())
    return net, recent


def apply_signal_shock(forecast_df, signals_df, manual_shock_pct=0.0,
                       commodity_group="Pet Coke", today=None):
    """
    Returns (adjusted_df, info)

    adjusted_df keeps the baseline (yhat, yhat_lower, yhat_upper) and adds
    yhat_signal_adjusted, yhat_lower_adj, yhat_upper_adj.

    manual_shock_pct: e.g. 0.10 for +10% (from the dashboard slider).
    """
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    net, used = signal_score(signals_df, commodity_group, today)

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
