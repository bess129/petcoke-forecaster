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

from forecaster import run_forecast, COMMODITIES, DATA_DIR
from shock import apply_signal_shock, LOOKBACK_DAYS, MIN_URGENCY

SIGNALS_CSV = DATA_DIR / "signals.csv"
UNITS = {"Pet Coke": "PPI index (Dec 1984 = 100)", "Cobalt": "USD per metric ton (LME, 99.8%)",
         "Antimony": "USD per kg (US import unit value, UN Comtrade)",
         "Gallium/Germanium (HS 811292)": "USD per kg (US import unit value, UN Comtrade)"}   # others: whatever unit your CSV is in

st.set_page_config(page_title="Early-Warning Forecaster", layout="wide")


# ---------------------------------------------------------------- data loaders
@st.cache_data(ttl=3600, show_spinner="Running forecast...")
def get_forecast(commodity):
    return run_forecast(commodity)


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
group = "Pet Coke" if commodity == "Pet Coke" else "Minor Metals"

shock = st.sidebar.slider("Simulate Supply Shock (%)", -20, 20, 0, step=1,
                          help="Manually bend the forecast up or down over the next 30 days.")

st.sidebar.markdown("---")
if st.sidebar.button("Refresh news & Claude signals"):
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
                               commodity_group=group)

today = pd.Timestamp.today().normalize()
target = today + pd.Timedelta(days=30)
row30 = adj.iloc[(adj["ds"] - target).abs().argmin()]
current = hist["y"].iloc[-1]

# KPI cards
c1, c2, c3, c4 = st.columns(4)
c1.metric("Current Price", f"{current:,.1f}",
          help=f"Last actual value ({hist['ds'].iloc[-1]:%b %Y}). Units: "
               f"{UNITS.get(commodity, 'as in your CSV')}")
c2.metric("30-Day Baseline Forecast", f"{row30['yhat']:,.1f}",
          f"{(row30['yhat'] / current - 1):+.1%}")
c3.metric("Active Weak Signal Score", f"{info['net_score']:+.2f}")
c3.caption(f"{info['n_signals']} high-urgency signal(s) in the last {LOOKBACK_DAYS} days")
alert = info["regime"] != "Normal"
c4.metric("Regime Status", "High Alert" if alert else "Normal",
          f"{info['total_shift_pct']:+.1%} adjustment" if alert else None,
          delta_color="inverse" if info["total_shift_pct"] > 0 else "normal")
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

# Weak Signal Radar
st.subheader("Weak Signal Radar")
if signals.empty:
    st.info("No scored signals yet. Run `py scraper.py` then `py signal_detector.py`, "
            "or click **Refresh news & Claude signals** in the sidebar.")
else:
    radar = (signals[signals["commodity"] == group]
             .sort_values(["urgency_score", "published_date"], ascending=False))
    hide_noise = st.checkbox("Hide 'Noise'", value=True)
    if hide_noise:
        radar = radar[radar["signal_category"] != "Noise"]

    view = radar[["urgency_score", "published_date", "signal_category",
                  "price_direction", "title", "reasoning", "source", "link"]].rename(columns={
        "urgency_score": "Urgency", "published_date": "Date", "signal_category": "Category",
        "price_direction": "Direction", "title": "Headline", "reasoning": "Why it matters",
        "source": "Source", "link": "Link"})

    colors = {5: "#f4a3a3", 4: "#f8c99b", 3: "#fbe7a1", 2: "#e8f0d8", 1: "#f2f2f2"}
    styled = (view.style
              .apply(lambda r: [f"background-color: {colors.get(r['Urgency'], '')}"] * len(r), axis=1)
              .format({"Direction": "{:+.1f}", "Date": lambda d: f"{d:%Y-%m-%d}" if pd.notna(d) else ""}))
    st.dataframe(styled, width="stretch", hide_index=True, height=420,
                 column_config={"Link": st.column_config.LinkColumn(display_text="open")})
    st.caption(f"{len(view)} signals shown for {group}. Colour = urgency (red = 5).")
