"""
app.py - Minor Metals & Pet Coke Early-Warning Forecaster dashboard (Step 7).

Run with:
    py -m streamlit run app.py
"""

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import os

# Online (Streamlit Community Cloud) the API key comes from the app's Secrets
# settings, not from Windows. Copy it into the environment so the anthropic
# library finds it. Locally this does nothing if no secrets file exists.
try:
    if not os.environ.get("ANTHROPIC_API_KEY") and "ANTHROPIC_API_KEY" in st.secrets:
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

from forecaster import run_forecast, backtest, COMMODITIES, DATA_DIR
from shock import apply_signal_shock, signals_for, LOOKBACK_DAYS, MIN_URGENCY

SIGNALS_CSV = DATA_DIR / "signals.csv"
UNITS = {"Pet Coke": "PPI index (Dec 1984 = 100)", "Cobalt": "USD per metric ton (LME, 99.8%)",
         "Antimony": "USD per kg (US import unit value, UN Comtrade)",
         "Gallium/Germanium (HS 811292)": "USD per kg (US import unit value, UN Comtrade)"}   # others: whatever unit your CSV is in

st.set_page_config(page_title="Early-Warning Forecaster", layout="wide")


# ---------------------------------------------------------------- data loaders
@st.cache_data(ttl=3600, show_spinner="Running forecast...")
def get_forecast(commodity):
    return run_forecast(commodity)


@st.cache_data(ttl=86400, show_spinner="Running backtest...")
def get_backtest(commodity):
    return backtest(commodity)


def _admin_password():
    """Set ADMIN_PASSWORD in Streamlit Secrets to protect the Refresh button
    online (it spends Claude API credit). Locally there is no password."""
    try:
        return st.secrets.get("ADMIN_PASSWORD")
    except Exception:
        return None


@st.cache_data(ttl=600)
def get_signals(_mtime):
    if not SIGNALS_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(SIGNALS_CSV)
    df["published_date"] = pd.to_datetime(df["published_date"], errors="coerce")
    return df


def refresh_news_and_signals():
    from scraper import fetch_news
    from signal_detector import analyze_headlines
    news = fetch_news()
    news.to_csv(DATA_DIR / "news.csv", index=False)
    analyze_headlines(news)


# ---------------------------------------------------------------- sidebar
st.sidebar.title("Controls")
commodity = st.sidebar.selectbox("Commodity", list(COMMODITIES))

shock = st.sidebar.slider("Simulate Supply Shock (%)", -20, 20, 0, step=1,
                          help="Manually bend the forecast up or down over the next 30 days.")

st.sidebar.markdown("---")
required_pw = _admin_password()
entered_pw = (st.sidebar.text_input("Admin password (to refresh)", type="password")
              if required_pw else None)
can_refresh = (not required_pw) or entered_pw == required_pw
if st.sidebar.button("Refresh news & Claude signals", disabled=not can_refresh):
    with st.spinner("Fetching Google News and scoring with Claude..."):
        try:
            refresh_news_and_signals()
            st.sidebar.success("Signals updated.")
        except Exception as e:
            st.sidebar.error(f"Refresh failed: {e}")
st.sidebar.caption(f"Regime shift uses urgency >= {MIN_URGENCY} signals "
                   f"from the last {LOOKBACK_DAYS} days.")

# ---------------------------------------------------------------- main
st.title("Minor Metals & Pet Coke Early-Warning Forecaster")

csv_path = DATA_DIR / COMMODITIES[commodity]["file"]
if not csv_path.exists():
    st.warning(f"No data for **{commodity}** yet. Add a CSV with columns `date, price` "
               f"at `data/{csv_path.name}` and reload.")
    st.stop()

hist, fc, model_used = get_forecast(commodity)
mtime = SIGNALS_CSV.stat().st_mtime if SIGNALS_CSV.exists() else 0
signals = get_signals(mtime)

adj, info = apply_signal_shock(fc, signals, manual_shock_pct=shock / 100,
                               commodity=commodity)

today = pd.Timestamp.today().normalize()
target = today + pd.Timedelta(days=30)
row30 = adj.iloc[(adj["ds"] - target).abs().argmin()]
current = hist["y"].iloc[-1]
last_data = hist["ds"].iloc[-1]

# KPI cards
c1, c2, c3, c4 = st.columns(4)
c1.metric(f"Latest Price ({last_data:%b %Y})", f"{current:,.1f}",
          help=f"Most recent published value. Units: {UNITS.get(commodity, 'as in your CSV')}")
c2.metric(f"Baseline Forecast ({target:%d %b %Y})", f"{row30['yhat']:,.1f}",
          f"{(row30['yhat'] / current - 1):+.1%} vs latest",
          help="Forecast for today + 30 days, before any weak-signal adjustment.")
c3.metric("Active Weak Signal Score", f"{info['net_score']:+.2f}")
c3.caption(f"{info['n_signals']} high-urgency signal(s) in the last {LOOKBACK_DAYS} days")
alert = info["regime"] != "Normal"
c4.metric("Regime Status", "High Alert" if alert else "Normal",
          f"{info['total_shift_pct']:+.1%} adjustment" if alert else None,
          delta_color="inverse" if info["total_shift_pct"] > 0 else "normal")
st.caption(f"Price data is published with a delay: the latest available month is "
           f"**{last_data:%B %Y}**. The forecast runs from there to **{target:%d %B %Y}** "
           f"(today + 30 days).")
if alert:
    st.error(f"**Regime Shift:** forecast adjusted {info['total_shift_pct']:+.1%} over 30 days "
             f"(signals {info['signal_shift_pct']:+.1%}, manual {info['manual_shift_pct']:+.1%}).")

# Chart
fig = go.Figure()
recent_hist = hist[hist["ds"] >= hist["ds"].max() - pd.DateOffset(years=3)]
fig.add_trace(go.Scatter(x=recent_hist["ds"], y=recent_hist["y"], name="Historical",
                         line=dict(color="#1f3b57", width=2)))
fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat_upper_adj"], line=dict(width=0),
                         showlegend=False, hoverinfo="skip"))
fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat_lower_adj"], line=dict(width=0),
                         fill="tonexty", fillcolor="rgba(214,96,77,0.18)",
                         name="Weak-Signal Adjusted cone"))
fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat"], name="Baseline forecast",
                         line=dict(color="#7f7f7f", dash="dash")))
fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat_signal_adjusted"],
                         name="Signal-adjusted forecast", line=dict(color="#d6604d", width=2)))
fig.add_vline(x=last_data, line_dash="dot", line_color="#1f3b57")
fig.add_annotation(x=last_data, y=0.98, yref="paper", text="Last data", showarrow=False,
                   xanchor="right", xshift=-4, font=dict(color="gray"))
fig.add_vline(x=today, line_dash="dot", line_color="gray")
fig.add_annotation(x=today, y=0.02, yref="paper", text="Today", showarrow=False,
                   xanchor="right", xshift=-4, font=dict(color="gray"))
# Open zoomed in on the last 12 months + forecast so the 30-day window is visible.
fig.update_xaxes(range=[hist["ds"].max() - pd.DateOffset(months=12),
                        adj["ds"].max() + pd.Timedelta(days=5)])
fig.update_layout(height=480, margin=dict(l=10, r=10, t=50, b=10), hovermode="x unified",
                  yaxis_title=UNITS.get(commodity, "Price"),
                  legend=dict(orientation="h", y=-0.15))
st.plotly_chart(fig, width="stretch")
st.caption(f"Model: {model_used}. Shaded cone = 80% interval after weak-signal adjustment.")

# Backtest
with st.expander("How accurate is this forecast? (backtest)"):
    try:
        bt_table, bt = get_backtest(commodity)
    except Exception as e:
        st.info(f"Backtest not available: {e}")
    else:
        st.markdown(
            f"We hid the last **{bt['holdout_months']} months** of real prices, forecast them "
            f"using only data up to **{bt['train_end']:%b %Y}**, and compared with what "
            f"actually happened. Benchmark: a *no-change* guess (last known price stays flat).")
        b1, b2, b3 = st.columns(3)
        b1.metric(f"{bt['model_name']} error", f"{bt['model_mape']:.1f}%",
                  help="Mean absolute percentage error (MAPE): average % miss per month.")
        b2.metric("No-change guess error", f"{bt['naive_mape']:.1f}%")
        b3.metric("Beats the simple guess?", "Yes" if bt["beats_naive"] else "No")
        bfig = go.Figure()
        ctx = hist[(hist["ds"] > bt["train_end"] - pd.DateOffset(months=12))
                   & (hist["ds"] <= bt["train_end"])]
        bfig.add_trace(go.Scatter(x=ctx["ds"], y=ctx["y"], name="Training data",
                                  line=dict(color="#1f3b57")))
        bfig.add_trace(go.Scatter(x=bt_table["ds"], y=bt_table["actual"], name="What happened",
                                  line=dict(color="#1f3b57", width=3), mode="lines+markers"))
        bfig.add_trace(go.Scatter(x=bt_table["ds"], y=bt_table["model"], name="Model forecast",
                                  line=dict(color="#d6604d", width=2)))
        bfig.add_trace(go.Scatter(x=bt_table["ds"], y=bt_table["naive"], name="No-change guess",
                                  line=dict(color="#7f7f7f", dash="dash")))
        bfig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                           hovermode="x unified", legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(bfig, width="stretch")
        if not bt["beats_naive"]:
            st.caption("The model did not beat the no-change guess over this period. This is "
                       "common for illiquid commodities driven by one-off policy events, which "
                       "is exactly why the weak-signal overlay exists.")

# Weak Signal Radar
st.subheader("Weak Signal Radar")
if signals.empty:
    st.info("No scored signals yet. Run `py scraper.py` then `py signal_detector.py`, "
            "or click **Refresh news & Claude signals** in the sidebar.")
else:
    radar = (signals_for(signals, commodity)
             .sort_values(["urgency_score", "published_date"], ascending=False))
    hide_noise = st.checkbox("Hide 'Noise'", value=True)
    if hide_noise:
        radar = radar[radar["signal_category"] != "Noise"]

    cols = ["urgency_score", "published_date", "signal_category", "price_direction",
            "title", "reasoning", "source", "link"]
    if commodity != "Pet Coke":
        cols.insert(4, "applies_to")
    view = radar[cols].rename(columns={"applies_to": "Applies to",
        "urgency_score": "Urgency", "published_date": "Date", "signal_category": "Category",
        "price_direction": "Direction", "title": "Headline", "reasoning": "Why it matters",
        "source": "Source", "link": "Link"})

    colors = {5: "#f4a3a3", 4: "#f8c99b", 3: "#fbe7a1", 2: "#e8f0d8", 1: "#f2f2f2"}
    styled = (view.style
              .apply(lambda r: [f"background-color: {colors.get(r['Urgency'], '')}; color: #111"] * len(r), axis=1)
              .format({"Direction": "{:+.1f}", "Date": lambda d: f"{d:%Y-%m-%d}" if pd.notna(d) else ""}))
    st.dataframe(styled, width="stretch", hide_index=True, height=420,
                 column_config={"Link": st.column_config.LinkColumn(display_text="open")})
    st.caption(f"{len(view)} signals shown for {commodity}. Colour = urgency (red = 5). "
               + ("Metals get headlines that name them plus general critical-minerals news."
                  if commodity != "Pet Coke" else ""))
