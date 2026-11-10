"""
app.py - Minor Metals & Pet Coke Early-Warning Forecaster dashboard.

Run with:
    py -m streamlit run app.py
"""

import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Online (Streamlit Community Cloud) the API key comes from the app's Secrets
# settings, not from Windows. Copy it into the environment so the anthropic
# library finds it. Locally this does nothing if no secrets file exists.
try:
    if not os.environ.get("ANTHROPIC_API_KEY") and "ANTHROPIC_API_KEY" in st.secrets:
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

from forecaster import forecast_and_evaluate, load_history, COMMODITIES, DATA_DIR
from shock import (apply_signal_shock, signals_for, supply_outlook,
                   LOOKBACK_DAYS, MIN_URGENCY, OUTLOOK_DAYS, SUPPLY_CATEGORIES)
from outlook import load_outlook, OUTLOOK_JSON

SIGNALS_CSV = DATA_DIR / "signals.csv"
UNITS = {
    "Green Pet Coke (GPC)": "USD per tonne (US export unit value, HS 271311, UN Comtrade)",
    "Calcined Pet Coke (CPC)": "USD per tonne (US export unit value, HS 271312, UN Comtrade)",
    "Pet Coke PPI index (broad)": "PPI index (Dec 1984 = 100)",
    "Cobalt": "USD per tonne (IMF, LME 99.8%)",
    "Molybdenum": "USD per tonne (IMF, roasted concentrate)",
    "Lithium": "USD per tonne (IMF, battery-grade metal)",
    "Rare Earths": "USD per tonne (IMF, carbonate REO 42-45%)",
    "Tin": "USD per tonne (IMF, LME)",
    "Antimony": "USD per kg (US import unit value, UN Comtrade)",
    "Gallium/Germanium (HS 811292)": "USD per kg (US import unit value, UN Comtrade)",
}

st.set_page_config(page_title="Early-Warning Forecaster", layout="wide")


# ---------------------------------------------------------------- data loaders
@st.cache_data(ttl=6 * 3600, show_spinner="Testing models on past data and forecasting...")
def get_forecast(commodity):
    return forecast_and_evaluate(commodity)


@st.cache_data(ttl=3600)
def get_histories():
    return {c: load_history(DATA_DIR / v["file"]) for c, v in COMMODITIES.items()
            if (DATA_DIR / v["file"]).exists()}


@st.cache_data(ttl=600)
def get_signals(_mtime):
    if not SIGNALS_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(SIGNALS_CSV)
    df["published_date"] = pd.to_datetime(df["published_date"], errors="coerce")
    return df


@st.cache_data(ttl=600)
def get_outlook(_mtime):
    return load_outlook()


def _admin_password():
    """Set ADMIN_PASSWORD in Streamlit Secrets to protect the Refresh button
    online (it spends Claude API credit). Locally there is no password."""
    try:
        return st.secrets.get("ADMIN_PASSWORD")
    except Exception:
        return None


def refresh_all():
    from scraper import fetch_news
    from signal_detector import analyze_headlines
    from outlook import generate_outlook
    news = fetch_news()
    news.to_csv(DATA_DIR / "news.csv", index=False)
    analyze_headlines(news)
    generate_outlook()


def _mtime(path):
    return path.stat().st_mtime if path.exists() else 0


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
if st.sidebar.button("Refresh news, signals & outlook", disabled=not can_refresh):
    with st.spinner("Fetching news, scoring with Claude, writing outlook..."):
        try:
            refresh_all()
            st.sidebar.success("Updated.")
        except Exception as e:
            st.sidebar.error(f"Refresh failed: {e}")
st.sidebar.caption(f"News refreshes automatically every morning. Regime shift uses "
                   f"urgency >= {MIN_URGENCY} signals from the last {LOOKBACK_DAYS} days.")

signals = get_signals(_mtime(SIGNALS_CSV))
histories = get_histories()
today = pd.Timestamp.today().normalize()

st.title("Minor Metals & Pet Coke Early-Warning Forecaster")
tab_fc, tab_risk = st.tabs(["Forecast", "Supply Risk Outlook (all commodities)"])

# ======================================================================
# TAB 1: forecast for the selected commodity
# ======================================================================
with tab_fc:
    if commodity not in histories:
        st.warning(f"No data for **{commodity}** yet. Add a CSV with columns `date, price` "
                   f"at `data/{COMMODITIES[commodity]['file']}` and reload.")
        st.stop()

    hist, fc, mdl = get_forecast(commodity)
    adj, info = apply_signal_shock(fc, signals, manual_shock_pct=shock / 100,
                                   commodity=commodity)
    target = today + pd.Timedelta(days=30)
    row30 = adj.iloc[(adj["ds"] - target).abs().argmin()]
    current = hist["y"].iloc[-1]
    last_data = hist["ds"].iloc[-1]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Latest Price ({last_data:%b %Y})", f"{current:,.1f}",
              help=f"Most recent published value. Units: {UNITS.get(commodity, 'as in your CSV')}")
    c2.metric(f"Forecast, {target:%d %b}", f"{row30['yhat']:,.1f}",
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

    # ---- chart with news markers
    fig = go.Figure()
    recent_hist = hist[hist["ds"] >= last_data - pd.DateOffset(years=3)]
    fig.add_trace(go.Scatter(x=recent_hist["ds"], y=recent_hist["y"], name="Historical",
                             line=dict(color="#1f3b57", width=2)))
    fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat_upper_adj"], line=dict(width=0),
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat_lower_adj"], line=dict(width=0),
                             fill="tonexty", fillcolor="rgba(214,96,77,0.18)",
                             name="80% range (from past errors)"))
    fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat"], name="Baseline forecast",
                             line=dict(color="#7f7f7f", dash="dash")))
    fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat_signal_adjusted"],
                             name="Signal-adjusted forecast", line=dict(color="#d6604d", width=2)))

    # News markers: where each relevant headline falls on the price/forecast line
    news = signals_for(signals, commodity) if not signals.empty else pd.DataFrame()
    if not news.empty:
        news = news[(news["urgency_score"] >= 3) & (news["signal_category"] != "Noise")
                    & (news["published_date"] >= last_data - pd.DateOffset(months=12))].copy()
    if not news.empty:
        line = pd.concat([hist.set_index("ds")["y"],
                          adj.set_index("ds")["yhat_signal_adjusted"]]).sort_index()
        line = line[~line.index.duplicated()]
        to_ns = lambda t: np.asarray(t, dtype="datetime64[ns]").astype("int64")
        news["y"] = np.interp(to_ns(news["published_date"].dt.normalize()),
                              to_ns(line.index), line.values.astype(float))
        for label, mask, color, symbol in [
                ("News: bullish", news["price_direction"] > 0.1, "#1a9850", "triangle-up"),
                ("News: bearish", news["price_direction"] < -0.1, "#b2182b", "triangle-down"),
                ("News: neutral", news["price_direction"].abs() <= 0.1, "#999999", "circle")]:
            n = news[mask]
            if n.empty:
                continue
            fig.add_trace(go.Scatter(
                x=n["published_date"], y=n["y"], mode="markers", name=label,
                marker=dict(color=color, symbol=symbol, size=8 + 3 * (n["urgency_score"] - 3),
                            line=dict(width=1, color="white")),
                text=[f"{d:%d %b}: [{c}, urgency {u}] {t[:90]}" for d, c, u, t in
                      zip(n["published_date"], n["signal_category"], n["urgency_score"], n["title"])],
                hovertemplate="%{text}<extra></extra>"))

    fig.add_vline(x=last_data, line_dash="dot", line_color="#1f3b57")
    fig.add_annotation(x=last_data, y=0.98, yref="paper", text="Last data", showarrow=False,
                       xanchor="right", xshift=-4, font=dict(color="gray"))
    fig.add_vline(x=today, line_dash="dot", line_color="gray")
    fig.add_annotation(x=today, y=0.02, yref="paper", text="Today", showarrow=False,
                       xanchor="right", xshift=-4, font=dict(color="gray"))
    fig.update_xaxes(range=[last_data - pd.DateOffset(months=12),
                            adj["ds"].max() + pd.Timedelta(days=5)])
    fig.update_layout(height=500, margin=dict(l=10, r=10, t=50, b=10), hovermode="closest",
                      yaxis_title=UNITS.get(commodity, "Price"),
                      legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, width="stretch")
    st.caption(f"Model: **{mdl['model']}** (chosen automatically, see below). Triangles = "
               f"news headlines (urgency 3+): green = supply/bullish, red = bearish; "
               f"bigger = more urgent. Hover to read.")

    # ---- model selection / accuracy
    with st.expander("How accurate is this forecast? (model selection on past data)"):
        lb = mdl["leaderboard"].copy()
        cv = mdl["cv"]
        nochange = float(lb.set_index("model").loc["No-change", "avg_error_pct"])
        st.markdown(
            f"Every model was tested **{mdl['folds']} times**: we pretended it was an earlier "
            f"month, forecast the next **{mdl['horizon']} months** using only data known then, "
            f"and measured the average % miss. The model with the smallest error is used. "
            f"The shaded range on the chart comes from the chosen model's real past misses.")
        b1, b2, b3 = st.columns(3)
        b1.metric("Chosen model", mdl["model"])
        b2.metric("Its average error", f"{lb['avg_error_pct'].iloc[0]:.1f}%")
        b3.metric("No-change guess error", f"{nochange:.1f}%",
                  help="Benchmark: last known price stays flat.")
        lb.insert(0, "Rank", range(1, len(lb) + 1))
        lb["vs no-change"] = (lb["avg_error_pct"] - nochange).map(lambda v: f"{v:+.1f} pts")
        st.dataframe(lb.rename(columns={"model": "Model", "avg_error_pct": "Avg error (%)"}),
                     hide_index=True, width="stretch")

        last_origin = cv["origin"].max()
        fold = cv[cv["origin"] == last_origin].copy()
        fold["ds"] = [last_origin + pd.DateOffset(months=int(h)) for h in fold["h"]]
        bfig = go.Figure()
        ctx = hist[(hist["ds"] > last_origin - pd.DateOffset(months=12))]
        bfig.add_trace(go.Scatter(x=ctx["ds"], y=ctx["y"], name="Actual price",
                                  line=dict(color="#1f3b57", width=3)))
        for name, color, dash in [(mdl["model"], "#d6604d", "solid"),
                                  ("No-change", "#7f7f7f", "dash")]:
            f = fold[fold["model"] == name]
            if not f.empty:
                bfig.add_trace(go.Scatter(x=f["ds"], y=f["pred"],
                                          name=f"{name} (forecast made {last_origin:%b %Y})",
                                          line=dict(color=color, dash=dash), mode="lines+markers"))
        bfig.add_vline(x=last_origin, line_dash="dot", line_color="gray")
        bfig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                           hovermode="x unified", legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(bfig, width="stretch")
        if mdl["model"] == "No-change":
            st.caption("No model beat the simple 'price stays the same' guess for this commodity. "
                       "That is common for illiquid metals moved by one-off policy events, which is "
                       "why the news signals (Supply Risk Outlook tab) matter most here.")

    # ---- Weak Signal Radar
    st.subheader("Weak Signal Radar")
    if signals.empty:
        st.info("No scored signals yet. Run `py scraper.py` then `py signal_detector.py`.")
    else:
        radar = (signals_for(signals, commodity)
                 .sort_values(["urgency_score", "published_date"], ascending=False))
        if st.checkbox("Hide 'Noise'", value=True):
            radar = radar[radar["signal_category"] != "Noise"]
        cols = ["urgency_score", "published_date", "signal_category", "price_direction",
                "title", "reasoning", "source", "link"]
        if not "Pet Coke" in commodity:
            cols.insert(4, "applies_to")
        view = radar[cols].rename(columns={
            "applies_to": "Applies to", "urgency_score": "Urgency", "published_date": "Date",
            "signal_category": "Category", "price_direction": "Direction", "title": "Headline",
            "reasoning": "Why it matters", "source": "Source", "link": "Link"})
        colors = {5: "#f4a3a3", 4: "#f8c99b", 3: "#fbe7a1", 2: "#e8f0d8", 1: "#f2f2f2"}
        styled = (view.style
                  .apply(lambda r: [f"background-color: {colors.get(r['Urgency'], '')}; color: #111"]
                         * len(r), axis=1)
                  .format({"Direction": "{:+.1f}",
                           "Date": lambda d: f"{d:%Y-%m-%d}" if pd.notna(d) else ""}))
        st.dataframe(styled, width="stretch", hide_index=True, height=420,
                     column_config={"Link": st.column_config.LinkColumn(display_text="open")})
        st.caption(f"{len(view)} signals shown for {commodity}. Colour = urgency (red = 5)."
                   + (" Metals get headlines that name them, plus general critical-minerals "
                      "news (marked 'All metals (general)')." if not "Pet Coke" in commodity else ""))

# ======================================================================
# TAB 2: which commodities could rise because of supply shortages?
# ======================================================================
with tab_risk:
    st.markdown(
        f"**Which commodities could see prices rise because of supply shortages?** "
        f"Ranked by supply pressure in the last **{OUTLOOK_DAYS} days** of news: outages, "
        f"export rules, host-metal cuts, refinery turnarounds and logistics. Each headline is "
        f"weighted by its urgency. **Only headlines that name the metal** can mark it as upside "
        f"risk; general critical-minerals news adds a small background amount.")
    if signals.empty:
        st.info("No scored news yet.")
    else:
        rank = supply_outlook(signals, histories, today)
        ol = get_outlook(_mtime(OUTLOOK_JSON))
        claude = {o["commodity"]: o for o in (ol or {}).get("outlooks", [])}
        # outlooks written before the GPC rename used the old names
        for new, old in {"Green Pet Coke (GPC)": "Pet Coke (fuel-grade)",
                         "Calcined Pet Coke (CPC)": "Pet Coke (calcined)",
                         "Pet Coke PPI index (broad)": "Pet Coke (PPI index)"}.items():
            if new not in claude and old in claude:
                claude[new] = claude[old]

        bar = rank.sort_values("risk_score")
        rfig = go.Figure(go.Bar(
            x=bar["risk_score"], y=bar["commodity"], orientation="h",
            marker_color=["#1a9850" if v > 0.10 else "#b2182b" if v < -0.10 else "#999999"
                          for v in bar["risk_score"]],
            text=[f"{v:+.2f}" for v in bar["risk_score"]], textposition="outside",
            hovertemplate="%{y}: %{x:+.2f}<extra></extra>"))
        rfig.add_vline(x=0.10, line_dash="dot", line_color="#1a9850")
        rfig.add_vline(x=-0.10, line_dash="dot", line_color="#b2182b")
        lim = max(0.15, float(bar["risk_score"].abs().max()) * 1.25)
        rfig.update_xaxes(range=[-lim, lim])
        rfig.update_layout(height=40 * len(bar) + 80, margin=dict(l=10, r=40, t=30, b=10),
                           xaxis_title="Supply-shortage risk score (−1 bearish … +1 strong shortage)",
                           title="Supply-shortage risk score")
        st.plotly_chart(rfig, width="stretch")

        table = pd.DataFrame({
            "Rank": range(1, len(rank) + 1),
            "Commodity": rank["commodity"],
            "News-based outlook": rank["outlook"],
            "Claude outlook": [f"{claude[c]['outlook']} ({claude[c]['confidence']})"
                               if c in claude else "–" for c in rank["commodity"]],
            "Risk score": rank["risk_score"],
            "Supply headlines: total (naming this metal)": [f"{a} ({b})" for a, b in
                                                  zip(rank["supply_signals"], rank["specific_signals"])],
            "Price 3m": rank["price_3m"],
            "Price 12m": rank["price_12m"],
            "Top driver": rank["top_driver"],
        })
        st.dataframe(table.style.format({"Risk score": "{:+.2f}", "Price 3m": "{:+.0%}",
                                         "Price 12m": "{:+.0%}"}, na_rep="–"),
                     hide_index=True, width="stretch")

        st.subheader("Claude analyst view")
        if not claude:
            st.info("No Claude outlook yet. It is written every morning by the daily refresh, "
                    "or click **Refresh news, signals & outlook** in the sidebar.")
        else:
            st.caption(f"Written {ol['generated_at'].replace('T', ' ')} UTC by {ol['model']} from "
                       f"the headlines above. Not investment advice.")
            for c in rank["commodity"]:
                o = claude.get(c)
                if not o:
                    continue
                icon = {"Upside risk": "▲", "Downside risk": "▼"}.get(o["outlook"], "●")
                with st.expander(f"{icon} {c}: {o['outlook']} ({o['confidence']} confidence)",
                                 expanded=o["outlook"] == "Upside risk" and o["confidence"] != "low"):
                    st.markdown(o["summary"])
                    if o.get("drivers"):
                        st.markdown("**Drivers:** " + "; ".join(o["drivers"]))
                    st.markdown(f"**Watch next:** {o['watch_next']}")

        with st.expander("How is the risk score calculated?"):
            st.markdown(
                f"- **Supply pressure** = urgency-weighted average of Claude's price direction "
                f"(−1 to +1) over supply-type headlines ({', '.join(sorted(SUPPLY_CATEGORIES))}) "
                f"from the last {OUTLOOK_DAYS} days.\n"
                f"- **Confidence** grows with the number of headlines **naming the metal** "
                f"(full at 5).\n"
                f"- **Risk score** = metal-specific pressure × confidence, plus a small background "
                f"from general critical-minerals news (at most about +0.06, so general news alone "
                f"can never reach the +0.10 upside-risk line).\n"
                f"- **Price 3m / 12m** show whether the price has already moved (a shortage may "
                f"already be priced in).\n"
                f"- The **Claude outlook** reads the same headlines plus price moves and gives a "
                f"short written view.")
