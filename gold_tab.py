"""
gold_tab.py - The "黄金 / Gold" page: live price, history, forecast, futures
market analysis (trend + CFTC positioning) and gold news.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import gold_data as gd
from i18n import t, is_zh, pick, fmt_date, fmt_month, category_name

UNIT = "USD/oz"


def _unit():
    return "美元/盎司" if is_zh() else "USD/oz"


def _pct(v, signed=True):
    if v is None or v != v:
        return "–"
    return f"{v:+.1%}" if signed else f"{v:.0%}"


def _arrow(v):
    if v is None or v != v or abs(v) < 0.0005:
        return "→"
    return "▲" if v > 0 else "▼"


def _card(label, value, sub=""):
    return (f"<div style='flex:1 1 150px;min-width:140px;padding:10px 12px;border:1px solid #e6e6e6;"
            f"border-radius:10px;background:#fafafa'><div style='font-size:0.82rem;color:#666'>{label}</div>"
            f"<div style='font-size:1.45rem;font-weight:600;line-height:1.3'>{value}</div>"
            f"<div style='font-size:0.82rem;color:#555'>{sub}</div></div>")


@st.cache_data(ttl=600, show_spinner=False)
def _quote():
    return gd.live_quote()


@st.cache_data(ttl=1800, show_spinner=False)
def _daily(_m):
    return gd.load_daily()


@st.cache_data(ttl=1800, show_spinner=False)
def _cot(_m):
    return gd.load_cot()


def _mtime(p):
    return p.stat().st_mtime if p.exists() else 0


def render(forecast_for, signals, signals_for, reliability_of):
    daily = _daily(_mtime(gd.DAILY_CSV))
    if daily.empty:
        st.info(t("gd_none"))
        return
    q = _quote()
    ts = gd.trend_stats(daily)

    # ------------------------------------------------------------ 1. live price
    st.markdown(t("gd_intro"))
    if q:
        when = pd.Timestamp(q["time"])
        when_txt = (when.strftime("%Y-%m-%d %H:%M") + (t("gd_sgt") if when.tzinfo else "")) if q["live"] \
            else when.strftime("%Y-%m-%d") + t("gd_close")
        cards = [
            _card(t("gd_price"), f"{q['price']:,.1f}", f"{_unit()} · {when_txt}"),
            _card(t("gd_today"), f"{_arrow(q['change'])} {_pct(q['change'])}",
                  t("gd_prev", p=f"{q['prev_close']:,.1f}")),
            _card(t("gd_1m"), f"{_arrow(ts['m1'])} {_pct(ts['m1'])}"),
            _card(t("gd_ytd"), f"{_arrow(ts['ytd'])} {_pct(ts['ytd'])}"),
            _card(t("gd_1y"), f"{_arrow(ts['y1'])} {_pct(ts['y1'])}"),
        ]
        st.markdown(f"<div style='display:flex;flex-wrap:wrap;gap:10px;margin:6px 0'>{''.join(cards)}</div>",
                    unsafe_allow_html=True)
        st.caption(t("gd_quote_note") if q["live"] else t("gd_quote_note_close"))
    try:
        import investing_links as il
        il.show_links("Gold")
        with st.expander(t("gd_inv_chart"), expanded=False):
            il.chart_widget("gold")
    except Exception:
        pass

    # ------------------------------------------------------------ 2. history
    st.subheader(t("gd_hist_title"))
    periods = {"1m": 31, "6m": 183, "1y": 366, "5y": 5 * 366, "all": None}
    p = st.radio(t("gd_period"), list(periods), index=2, horizontal=True,
                 format_func=lambda k: t("gd_p_" + k), key="gd_period")
    d = daily.copy()
    d["ma50"] = d["close"].rolling(50).mean()
    d["ma200"] = d["close"].rolling(200).mean()
    if periods[p]:
        d = d[d["date"] >= d["date"].iloc[-1] - pd.Timedelta(days=periods[p])]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d["date"], y=d["close"], name=t("gd_close_line"),
                             line=dict(color="#b8860b", width=2)))
    if p != "1m":
        fig.add_trace(go.Scatter(x=d["date"], y=d["ma50"], name=t("gd_ma50"),
                                 line=dict(color="#1f77b4", width=1.2, dash="dot")))
        fig.add_trace(go.Scatter(x=d["date"], y=d["ma200"], name=t("gd_ma200"),
                                 line=dict(color="#7f7f7f", width=1.2, dash="dash")))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=_unit(),
                      legend=dict(orientation="h", y=-0.15), hovermode="x unified")
    st.plotly_chart(fig, width="stretch")

    # ------------------------------------------------------------ 3. forecast
    st.subheader(t("gd_fc_title"))
    try:
        hist, fc, info = forecast_for("Gold")
        err, level = reliability_of(info)
        last = float(hist["y"].iloc[-1])
        rows = []
        for m in range(1, 7):
            target = hist["ds"].iloc[-1] + pd.DateOffset(months=m)
            if target > fc["ds"].max() + pd.Timedelta(days=3):
                break
            r = fc.iloc[(fc["ds"] - target).abs().argmin()]
            rows.append({t("gd_f_month"): fmt_month(target),
                         t("gd_f_fc"): f"{r['yhat']:,.0f}",
                         t("gd_f_range"): f"{r['yhat_lower']:,.0f} – {r['yhat_upper']:,.0f}",
                         t("gd_f_chg"): f"{_arrow(r['yhat'] / last - 1)} {_pct(r['yhat'] / last - 1)}"})
        st.markdown(t("gd_fc_intro", month=fmt_month(hist["ds"].iloc[-1]), last=f"{last:,.0f}",
                      err=f"{err:.1f}%", rel=t("rel_" + level)))
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        h3 = hist[hist["ds"] >= hist["ds"].iloc[-1] - pd.DateOffset(years=3)]
        ffig = go.Figure()
        ffig.add_trace(go.Scatter(x=h3["ds"], y=h3["y"], name=t("gd_monthly_avg"),
                                  line=dict(color="#b8860b", width=2)))
        ffig.add_trace(go.Scatter(x=fc["ds"], y=fc["yhat_upper"], line=dict(width=0), showlegend=False,
                                  hoverinfo="skip"))
        ffig.add_trace(go.Scatter(x=fc["ds"], y=fc["yhat_lower"], line=dict(width=0), fill="tonexty",
                                  fillcolor="rgba(184,134,11,0.18)", name=t("chart_range")))
        ffig.add_trace(go.Scatter(x=fc["ds"], y=fc["yhat"], name=t("chart_fc"),
                                  line=dict(color="#8b5a00", dash="dash")))
        ffig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=_unit(),
                           legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(ffig, width="stretch")
        st.caption(t("gd_fc_note"))
    except Exception as e:
        st.info(t("gd_fc_none", err=e))

    # ------------------------------------------------------------ 4. futures market analysis
    st.subheader(t("gd_fut_title"))
    cot = _cot(_mtime(gd.COT_CSV))
    cs = gd.cot_stats(cot)
    points = []
    # trend
    if ts.get("above200") is not None:
        points.append(t("gd_trend_up" if ts["above200"] else "gd_trend_down",
                        ma=f"{ts['ma200']:,.0f}", gap=_pct(ts["last"] / ts["ma200"] - 1)))
    if ts.get("cross"):
        points.append(t("gd_cross_" + ts["cross"]["type"], date=f"{ts['cross']['date']:%Y-%m-%d}"))
    points.append(t("gd_from_high", hi=f"{ts['hi52']:,.0f}", gap=_pct(ts["from_hi"])))
    vkey = "gd_vol_high" if ts["vol20"] > ts["vol1y"] * 1.25 else \
        "gd_vol_low" if ts["vol20"] < ts["vol1y"] * 0.8 else "gd_vol_normal"
    points.append(t(vkey, v20=_pct(ts["vol20"], False), v1y=_pct(ts["vol1y"], False)))
    # positioning
    if cs:
        pkey = "gd_pos_high" if cs["pct_3y"] >= 0.8 else "gd_pos_low" if cs["pct_3y"] <= 0.2 else "gd_pos_mid"
        points.append(t("gd_pos_now", date=fmt_date(cs["date"]), net=f"{cs['net']:,.0f}",
                        chg=f"{cs['net_chg_w']:+,.0f}", pct=f"{cs['pct_3y']:.0%}") + " " + t(pkey))
        points.append(t("gd_oi", oi=f"{cs['oi']:,.0f}", chg=_pct(cs["oi_chg_4w"])))
    st.markdown("\n".join(f"- {x}" for x in points))

    if cs:
        c3 = cot[cot["date"] >= cot["date"].iloc[-1] - pd.Timedelta(days=3 * 365)]
        px = daily.set_index("date")["close"]
        pfig = make_subplots(specs=[[{"secondary_y": True}]])
        pfig.add_trace(go.Bar(x=c3["date"], y=c3["mm_net"], name=t("gd_mm_net"), marker_color="#9db4c0"),
                       secondary_y=False)
        pfig.add_trace(go.Scatter(x=c3["date"], y=[float(px.asof(d_)) if d_ >= px.index[0] else np.nan
                                                  for d_ in c3["date"]],
                                  name=t("gd_close_line"), line=dict(color="#b8860b", width=2)),
                       secondary_y=True)
        pfig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), title=t("gd_cot_chart"),
                           legend=dict(orientation="h", y=-0.2))
        pfig.update_yaxes(title_text=t("gd_contracts"), secondary_y=False)
        pfig.update_yaxes(title_text=_unit(), secondary_y=True)
        st.plotly_chart(pfig, width="stretch")
    else:
        st.caption(t("gd_cot_none"))
    with st.expander(t("gd_fut_help_title")):
        st.markdown(t("gd_fut_help"))

    # ------------------------------------------------------------ 5. news
    st.subheader(t("gd_news_title"))
    s = signals_for(signals, "Gold") if signals is not None else pd.DataFrame()
    if s is None or s.empty:
        st.info(t("gd_news_none"))
    else:
        s = s.copy()
        s["published_date"] = pd.to_datetime(s["published_date"], errors="coerce")
        s = s[(s["signal_category"] != "Noise") &
              (s["published_date"] >= pd.Timestamp.today() - pd.Timedelta(days=14))]
        s = s.sort_values(["urgency_score", "published_date"], ascending=False).head(12)
        if s.empty:
            st.info(t("gd_news_none"))
        for _, r in s.iterrows():
            dirn = pd.to_numeric(r.get("price_direction"), errors="coerce")
            tag = t("gd_bull") if dirn > 0.05 else t("gd_bear") if dirn < -0.05 else t("gd_neutral")
            st.markdown(f"- **{tag}** · {r['published_date']:%m-%d} · "
                        f"[{pick(r.get('headline_zh'), r['title'])}]({r['link']}) "
                        f"<span style='color:#888'>（{category_name(r['signal_category'])} · {r['source']}）</span>"
                        if is_zh() else
                        f"- **{tag}** · {r['published_date']:%b %d} · [{r['title']}]({r['link']}) "
                        f"<span style='color:#888'>({category_name(r['signal_category'])} · {r['source']})</span>",
                        unsafe_allow_html=True)
    st.caption(t("gd_disclaimer"))
