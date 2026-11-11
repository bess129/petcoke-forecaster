"""
app.py - 小金属与石油焦价格预警系统 / Minor Metals & Pet Coke Early-Warning Forecaster.

Run with:
    py -m streamlit run app.py
"""

import io
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Online the API key comes from Streamlit Secrets; copy it into the environment.
try:
    if not os.environ.get("ANTHROPIC_API_KEY") and "ANTHROPIC_API_KEY" in st.secrets:
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

from forecaster import forecast_and_evaluate, load_history, COMMODITIES, DATA_DIR
from shock import (apply_signal_shock, signals_for, supply_outlook,
                   LOOKBACK_DAYS, OUTLOOK_DAYS, SUPPLY_CATEGORIES)
from outlook import load_outlook, OUTLOOK_JSON
from build_forecasts import load_cached, CACHE
from i18n import (t, set_lang, is_zh, pick, name_of, unit_of, model_name, category_name,
                  fmt_date, fmt_month, fmt_price)

SIGNALS_CSV = DATA_DIR / "signals.csv"
OLD_NAMES = {"Green Pet Coke (GPC)": "Pet Coke (fuel-grade)",
             "Calcined Pet Coke (CPC)": "Pet Coke (calcined)",
             "Pet Coke PPI index (broad)": "Pet Coke (PPI index)"}
COLORS = {"red": "#c0392b", "yellow": "#d4a017", "green": "#2e8b57"}
ICONS = {"red": "🔴", "yellow": "🟡", "green": "🟢"}

st.set_page_config(page_title="价格预警系统 | Price Early-Warning", page_icon="📈", layout="wide")

# ---------------------------------------------------------------- language
lang = st.sidebar.radio("语言 / Language", ["中文", "English"], horizontal=True, key="lang_choice")
set_lang("en" if lang == "English" else "zh")


# ---------------------------------------------------------------- data loaders
def _mtime(path):
    return path.stat().st_mtime if path.exists() else 0


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_forecast(commodity, _cache_mtime):
    cached = load_cached(commodity)
    return cached if cached is not None else forecast_and_evaluate(commodity)


@st.cache_data(ttl=3600)
def get_histories():
    return {c: load_history(DATA_DIR / v["file"]) for c, v in COMMODITIES.items()
            if (DATA_DIR / v["file"]).exists()}


@st.cache_data(ttl=600)
def get_signals(_mtime_):
    if not SIGNALS_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(SIGNALS_CSV)
    df["published_date"] = pd.to_datetime(df["published_date"], errors="coerce")
    for c in ["headline_zh", "reasoning_zh"]:
        if c not in df:
            df[c] = pd.NA
    return df


@st.cache_data(ttl=600)
def get_outlook(_mtime_):
    ol = load_outlook() or {}
    items = {o["commodity"]: o for o in ol.get("outlooks", [])}
    for new, old in OLD_NAMES.items():
        if new not in items and old in items:
            items[new] = items[old]
    return ol, items


def _admin_password():
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


# ---------------------------------------------------------------- sidebar
st.sidebar.markdown("---")
required_pw = _admin_password()
entered_pw = st.sidebar.text_input(t("admin_pw"), type="password") if required_pw else None
can_refresh = (not required_pw) or entered_pw == required_pw
if st.sidebar.button(t("refresh_btn"), disabled=not can_refresh):
    with st.spinner(t("refreshing")):
        try:
            refresh_all()
            st.cache_data.clear()
            st.sidebar.success(t("refresh_ok"))
        except Exception as e:
            st.sidebar.error(t("refresh_fail", err=e))
st.sidebar.caption(t("auto_refresh"))
st.sidebar.caption(t("disclaimer"))

# ---------------------------------------------------------------- shared data
today = pd.Timestamp.today().normalize()
target = today + pd.Timedelta(days=30)
signals = get_signals(_mtime(SIGNALS_CSV))
histories = get_histories()
ol_meta, claude = get_outlook(_mtime(OUTLOOK_JSON))
rank = (supply_outlook(signals, histories, today).set_index("commodity")
        if not signals.empty else pd.DataFrame())
available = [c for c in COMMODITIES if c in histories]


def forecast_for(c):
    return get_forecast(c, _mtime(CACHE))


def status_of(c, fc, hist):
    """Traffic light + reason, combining news risk, AI view and forecast change."""
    risk = float(rank.loc[c, "risk_score"]) if c in rank.index else 0.0
    specific = int(rank.loc[c, "specific_signals"]) if c in rank.index else 0
    o = claude.get(c, {})
    ai_up = o.get("outlook") == "Upside risk" and o.get("confidence") in ("medium", "high")
    row = fc.iloc[(fc["ds"] - target).abs().argmin()]
    chg = row["yhat"] / hist["y"].iloc[-1] - 1
    if risk > 0.10 or ai_up:
        return "red", t("light_red"), t("light_red_why")
    if risk < -0.10 or chg <= -0.08:
        return "yellow", t("light_yellow"), t("light_yellow_why_down")
    if chg >= 0.08:
        return "yellow", t("light_yellow"), t("light_yellow_why_up")
    if risk >= 0.05 and specific >= 1:      # some news that names THIS commodity
        return "yellow", t("light_yellow"), t("light_yellow_why_news")
    return "green", t("light_green"), t("light_green_why")


def reliability_of(info):
    err = float(info["leaderboard"]["avg_error_pct"].iloc[0])
    level = t("rel_high") if err < 6 else t("rel_mid") if err < 12 else t("rel_low")
    return err, level


def summary_row(c):
    hist, fc, info = forecast_for(c)
    row = fc.iloc[(fc["ds"] - target).abs().argmin()]
    last = float(hist["y"].iloc[-1])
    light, label, why = status_of(c, fc, hist)
    err, level = reliability_of(info)
    return {"c": c, "hist": hist, "fc": fc, "info": info, "last": last,
            "last_month": hist["ds"].iloc[-1], "fc_val": float(row["yhat"]),
            "lo": float(row["yhat_lower"]), "hi": float(row["yhat_upper"]),
            "chg": float(row["yhat"]) / last - 1, "light": light, "label": label,
            "why": why, "err": err, "level": level}


st.title(t("app_title"))
tab_ov, tab_fc, tab_risk, tab_help = st.tabs(
    [t("tab_overview"), t("tab_forecast"), t("tab_risk"), t("tab_help")])

# ======================================================================
# OVERVIEW
# ======================================================================
with tab_ov:
    st.caption(t("overview_intro"))
    with st.spinner(t("computing")):
        rows = [summary_row(c) for c in available]

    # alerts first
    alerts = [r for r in rows if r["light"] in ("red", "yellow")]
    st.subheader(t("overview_alerts"))
    if not [r for r in rows if r["light"] == "red"]:
        st.success(t("overview_no_alerts"))
    for r in sorted(alerts, key=lambda r: r["light"] != "red"):
        msg = f"{ICONS[r['light']]} **{name_of(r['c'])}**: {r['why']}"
        (st.error if r["light"] == "red" else st.warning)(msg)

    st.markdown("---")
    cols = st.columns(2)
    for i, r in enumerate(rows):
        with cols[i % 2]:
            with st.container(border=True):
                st.markdown(f"### {ICONS[r['light']]} {name_of(r['c'])} "
                            f"<span style='color:{COLORS[r['light']]};font-size:0.8em'>"
                            f"· {r['label']}</span>", unsafe_allow_html=True)
                m1, m2 = st.columns(2)
                m1.metric(t("card_latest", month=fmt_month(r["last_month"])),
                          f"{fmt_price(r['last'])} {unit_of(r['c'])}")
                m2.metric(t("card_forecast", date=fmt_date(target)),
                          f"{fmt_price(r['fc_val'])} {unit_of(r['c'])}", f"{r['chg']:+.1%}")
                st.markdown(t("sentence", date=fmt_date(target), fc=fmt_price(r["fc_val"]),
                              unit=unit_of(r["c"]), chg=f"{r['chg']:+.1%}",
                              lo=fmt_price(r["lo"], r["last"]), hi=fmt_price(r["hi"], r["last"]),
                              why=r["why"]))
                st.caption(t("card_reliability", level=r["level"])
                           + f" (±{r['err']:.1f}%) · " + unit_of(r["c"], long=True))

    # Excel download
    st.markdown("---")
    summary = pd.DataFrame([{
        t("col_commodity"): name_of(r["c"]),
        t("col_signal"): r["label"],
        t("kpi_latest"): r["last"],
        t("col_date"): fmt_month(r["last_month"]),
        t("kpi_forecast"): round(r["fc_val"], 2),
        t("kpi_vs_latest", chg=""): f"{r['chg']:+.1%}",
        t("kpi_range"): f"{fmt_price(r['lo'], r['last'])} – {fmt_price(r['hi'], r['last'])}",
        t("reliability_title"): f"{r['level']} (±{r['err']:.1f}%)",
        t("col_model"): model_name(r["info"]["model"]),
        "Unit / 单位": unit_of(r["c"], long=True),
        t("col_claude"): pick(claude.get(r["c"], {}).get("summary_zh", ""),
                              claude.get(r["c"], {}).get("summary", "")),
    } for r in rows])
    tests = pd.concat([r["info"]["leaderboard"].assign(commodity=name_of(r["c"]))
                       .assign(model=lambda d: d["model"].map(model_name)) for r in rows])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        summary.to_excel(xl, sheet_name="Summary", index=False)
        tests.to_excel(xl, sheet_name="Model tests", index=False)
    st.download_button(t("download_excel"), buf.getvalue(),
                       file_name=f"price_outlook_{today:%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       help=t("download_help"))

# ======================================================================
# FORECAST (one commodity)
# ======================================================================
with tab_fc:
    commodity = st.selectbox(t("commodity"), available, format_func=name_of)
    hist, fc, mdl = forecast_for(commodity)
    shock = st.session_state.get("shock", 0)
    adj, info = apply_signal_shock(fc, signals, manual_shock_pct=shock / 100, commodity=commodity)
    row30 = adj.iloc[(adj["ds"] - target).abs().argmin()]
    current = float(hist["y"].iloc[-1])
    last_data = hist["ds"].iloc[-1]
    light, label, why = status_of(commodity, fc, hist)
    err, level = reliability_of(mdl)
    unit = unit_of(commodity)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(t("card_latest", month=fmt_month(last_data)), f"{fmt_price(current)} {unit}",
              help=t("kpi_latest_help", unit=unit_of(commodity, long=True)))
    k2.metric(t("kpi_forecast"), f"{fmt_price(row30['yhat_signal_adjusted'])} {unit}",
              t("kpi_vs_latest", chg=f"{row30['yhat_signal_adjusted'] / current - 1:+.1%}"),
              help=t("kpi_forecast_help", date=fmt_date(target)))
    k3.metric(t("kpi_range"), f"{fmt_price(row30['yhat_lower_adj'], current)} – "
                              f"{fmt_price(row30['yhat_upper_adj'], current)}",
              help=t("kpi_range_help"))
    k4.metric(t("kpi_signal"), f"{ICONS[light]} {label}")
    st.caption(t("data_delay", month=fmt_month(last_data), date=fmt_date(target)))
    if info["regime"] != "Normal":
        st.error(t("regime_alert", total=f"{info['total_shift_pct']:+.1%}",
                   sig=f"{info['signal_shift_pct']:+.1%}", man=f"{info['manual_shift_pct']:+.1%}"))

    # chart
    fig = go.Figure()
    recent = hist[hist["ds"] >= last_data - pd.DateOffset(years=3)]
    fig.add_trace(go.Scatter(x=recent["ds"], y=recent["y"], name=t("chart_hist"),
                             line=dict(color="#1f3b57", width=2)))
    fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat_upper_adj"], line=dict(width=0),
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat_lower_adj"], line=dict(width=0),
                             fill="tonexty", fillcolor="rgba(214,96,77,0.18)", name=t("chart_range")))
    if shock != 0 or info["signal_shift_pct"] != 0:
        fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat"], name=t("chart_fc"),
                                 line=dict(color="#7f7f7f", dash="dash")))
        fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat_signal_adjusted"], name=t("chart_fc_adj"),
                                 line=dict(color="#d6604d", width=2)))
    else:
        fig.add_trace(go.Scatter(x=adj["ds"], y=adj["yhat"], name=t("chart_fc"),
                                 line=dict(color="#d6604d", width=2)))
    news = signals_for(signals, commodity) if not signals.empty else pd.DataFrame()
    if not news.empty:
        marks = news[(news["urgency_score"] >= 3) & (news["signal_category"] != "Noise")
                     & (news["published_date"] >= last_data - pd.DateOffset(months=12))].copy()
        if not marks.empty:
            line = pd.concat([hist.set_index("ds")["y"],
                              adj.set_index("ds")["yhat_signal_adjusted"]]).sort_index()
            line = line[~line.index.duplicated()]
            to_ns = lambda x: np.asarray(x, dtype="datetime64[ns]").astype("int64")
            marks["y"] = np.interp(to_ns(marks["published_date"].dt.normalize()),
                                   to_ns(line.index), line.values.astype(float))
            for key, mask, color, symbol in [
                    ("chart_news_up", marks["price_direction"] > 0.1, "#1a9850", "triangle-up"),
                    ("chart_news_down", marks["price_direction"] < -0.1, "#b2182b", "triangle-down"),
                    ("chart_news_flat", marks["price_direction"].abs() <= 0.1, "#999999", "circle")]:
                n = marks[mask]
                if n.empty:
                    continue
                titles = [pick(z, e) for z, e in zip(n["headline_zh"], n["title"])]
                fig.add_trace(go.Scatter(
                    x=n["published_date"], y=n["y"], mode="markers", name=t(key),
                    marker=dict(color=color, symbol=symbol, size=8 + 3 * (n["urgency_score"] - 3),
                                line=dict(width=1, color="white")),
                    text=[f"{d:%Y-%m-%d} · {category_name(c)} · {u}/5<br>{tt[:80]}"
                          for d, c, u, tt in zip(n["published_date"], n["signal_category"],
                                                 n["urgency_score"], titles)],
                    hovertemplate="%{text}<extra></extra>"))
    fig.add_vline(x=last_data, line_dash="dot", line_color="#1f3b57")
    fig.add_annotation(x=last_data, y=0.98, yref="paper", text=t("chart_last"), showarrow=False,
                       xanchor="right", xshift=-4, font=dict(color="gray"))
    fig.add_vline(x=today, line_dash="dot", line_color="gray")
    fig.add_annotation(x=today, y=0.02, yref="paper", text=t("chart_today"), showarrow=False,
                       xanchor="right", xshift=-4, font=dict(color="gray"))
    fig.update_xaxes(range=[last_data - pd.DateOffset(months=12), adj["ds"].max() + pd.Timedelta(days=5)])
    fig.update_layout(height=460, margin=dict(l=10, r=10, t=40, b=10), hovermode="closest",
                      yaxis_title=unit, legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, width="stretch")
    st.caption(t("chart_caption"))

    # reliability in plain words
    nochange = float(mdl["leaderboard"].set_index("model").loc["No-change", "avg_error_pct"])
    rel = t("reliability_text", folds=mdl["folds"], h=mdl["horizon"], err=err)
    if mdl["model"] == "No-change":
        rel += "  \n" + t("reliability_nochange")
    else:
        rel += "  \n" + t("reliability_model", model=model_name(mdl["model"]), gain=nochange - err)
    st.info(f"**{t('reliability_title')}: {level}**  \n" + rel)

    # latest news (plain)
    st.subheader(t("latest_news"))
    if news.empty:
        st.caption(t("no_news"))
    else:
        top = (news[(news["signal_category"] != "Noise") & (news["urgency_score"] >= 2)]
               .sort_values(["published_date", "urgency_score"], ascending=False).head(5))
        if top.empty:
            st.caption(t("no_news"))
        for _, r in top.iterrows():
            arrow = "🟢▲" if r["price_direction"] > 0.1 else "🔴▼" if r["price_direction"] < -0.1 else "⚪●"
            title = pick(r.get("headline_zh"), r["title"])
            reason = pick(r.get("reasoning_zh"), r.get("reasoning", ""))
            date = f"{r['published_date']:%Y-%m-%d}" if pd.notna(r["published_date"]) else ""
            st.markdown(f"{arrow} **[{title}]({r['link']})**  \n"
                        f"<span style='color:gray;font-size:0.9em'>"
                        f"{t('news_line', date=date, cat=category_name(r['signal_category']), u=int(r['urgency_score']))}"
                        f" · {r.get('source', '')}</span>  \n{reason}", unsafe_allow_html=True)

    # advanced
    with st.expander(t("advanced")):
        st.slider(t("scenario"), -20, 20, 0, step=1, key="shock", help=t("scenario_help"))

        st.markdown(f"**{t('model_table')}**")
        lb = mdl["leaderboard"].copy()
        table = pd.DataFrame({
            t("col_rank"): range(1, len(lb) + 1),
            t("col_model"): lb["model"].map(model_name),
            t("col_err"): lb["avg_error_pct"],
            t("col_vs"): (lb["avg_error_pct"] - nochange).map(lambda v: f"{v:+.1f}"),
        })
        st.dataframe(table, hide_index=True, width="stretch")

        cv = mdl["cv"]
        last_origin = cv["origin"].max()
        fold = cv[cv["origin"] == last_origin].copy()
        fold["ds"] = [last_origin + pd.DateOffset(months=int(h)) for h in fold["h"]]
        bfig = go.Figure()
        ctx = hist[hist["ds"] > last_origin - pd.DateOffset(months=12)]
        bfig.add_trace(go.Scatter(x=ctx["ds"], y=ctx["y"], name=t("actual_price"),
                                  line=dict(color="#1f3b57", width=3)))
        lines = [(mdl["model"], "#d6604d", "solid")]
        if mdl["model"] != "No-change":
            lines.append(("No-change", "#7f7f7f", "dash"))
        for name, color, dash in lines:
            f = fold[fold["model"] == name]
            if not f.empty:
                bfig.add_trace(go.Scatter(x=f["ds"], y=f["pred"], name=model_name(name),
                                          line=dict(color=color, dash=dash), mode="lines+markers"))
        bfig.add_vline(x=last_origin, line_dash="dot", line_color="gray")
        bfig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                           title=t("backtest_chart", when=fmt_month(last_origin)),
                           hovermode="x unified", legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(bfig, width="stretch")

        st.markdown(f"**{t('all_news')}**")
        if not news.empty:
            radar = news.sort_values(["urgency_score", "published_date"], ascending=False)
            if st.checkbox(t("hide_noise"), value=True):
                radar = radar[radar["signal_category"] != "Noise"]
            view = pd.DataFrame({
                t("col_urgency"): radar["urgency_score"],
                t("col_date"): radar["published_date"].dt.strftime("%Y-%m-%d"),
                t("col_category"): radar["signal_category"].map(category_name),
                t("col_direction"): radar["price_direction"].map(lambda v: f"{v:+.1f}"),
                t("col_headline"): [pick(z, e) for z, e in zip(radar["headline_zh"], radar["title"])],
                t("col_why"): [pick(z, e) for z, e in zip(radar["reasoning_zh"], radar["reasoning"])],
                t("col_source"): radar["source"],
                t("col_link"): radar["link"],
            })
            if "Pet Coke" not in commodity:
                view.insert(4, t("col_applies"), radar["applies_to"].map(
                    {"This metal": t("applies_this"), "All metals (general)": t("applies_general")}))
            colors = {5: "#f4a3a3", 4: "#f8c99b", 3: "#fbe7a1", 2: "#e8f0d8", 1: "#f2f2f2"}
            ucol = t("col_urgency")
            styled = view.style.apply(
                lambda r: [f"background-color: {colors.get(r[ucol], '')}; color: #111"] * len(r), axis=1)
            st.dataframe(styled, width="stretch", hide_index=True, height=380,
                         column_config={t("col_link"): st.column_config.LinkColumn(display_text=t("open"))})

# ======================================================================
# SUPPLY RISK (all commodities)
# ======================================================================
with tab_risk:
    st.markdown(t("risk_intro", days=OUTLOOK_DAYS))
    if rank.empty:
        st.info(t("no_news"))
    else:
        r_sorted = rank.reset_index()
        bar = r_sorted.sort_values("risk_score")
        lim = max(0.15, float(bar["risk_score"].abs().max()) * 1.25)
        rfig = go.Figure(go.Bar(
            x=bar["risk_score"], y=[name_of(c) for c in bar["commodity"]], orientation="h",
            marker_color=[COLORS["green"] if v > 0.10 else "#b2182b" if v < -0.10 else "#999999"
                          for v in bar["risk_score"]],
            text=[f"{v:+.2f}" for v in bar["risk_score"]], textposition="outside",
            hovertemplate="%{y}: %{x:+.2f}<extra></extra>"))
        rfig.add_vline(x=0.10, line_dash="dot", line_color=COLORS["green"])
        rfig.add_vline(x=-0.10, line_dash="dot", line_color="#b2182b")
        rfig.update_xaxes(range=[-lim, lim], title=t("risk_axis"))
        rfig.update_layout(height=40 * len(bar) + 80, margin=dict(l=10, r=40, t=40, b=10),
                           title=t("risk_chart_title"))
        st.plotly_chart(rfig, width="stretch")

        def outlook_label(o):
            if not o:
                return "–"
            lab = {"Upside risk": t("outlook_up"), "Downside risk": t("outlook_down")}.get(
                o.get("outlook"), t("outlook_flat"))
            return f"{lab}（{t('conf_' + o.get('confidence', 'low'))}）" if is_zh() else \
                f"{lab} ({t('conf_' + o.get('confidence', 'low'))})"

        table = pd.DataFrame({
            t("col_rank"): range(1, len(r_sorted) + 1),
            t("col_commodity"): [name_of(c) for c in r_sorted["commodity"]],
            t("col_signal"): [{"Upside risk (supply)": t("light_red"), "Downside risk": t("outlook_down")}
                              .get(o, t("outlook_flat")) for o in r_sorted["outlook"]],
            t("col_claude"): [outlook_label(claude.get(c)) for c in r_sorted["commodity"]],
            t("col_score"): r_sorted["risk_score"].map(lambda v: f"{v:+.2f}"),
            t("col_headlines"): [f"{a} ({b})" for a, b in
                                 zip(r_sorted["supply_signals"], r_sorted["specific_signals"])],
            t("col_3m"): r_sorted["price_3m"].map(lambda v: "–" if pd.isna(v) else f"{v:+.0%}"),
            t("col_12m"): r_sorted["price_12m"].map(lambda v: "–" if pd.isna(v) else f"{v:+.0%}"),
            t("col_driver"): r_sorted["top_driver"],
        })
        st.dataframe(table, hide_index=True, width="stretch")

        st.subheader(t("claude_title"))
        if not claude:
            st.info(t("claude_none"))
        else:
            st.caption(t("claude_caption", model=ol_meta.get("model", ""),
                         when=str(ol_meta.get("generated_at", "")).replace("T", " ")))
            for c in r_sorted["commodity"]:
                o = claude.get(c)
                if not o:
                    continue
                icon = {"Upside risk": "▲", "Downside risk": "▼"}.get(o["outlook"], "●")
                with st.expander(f"{icon} {name_of(c)}: {outlook_label(o)}",
                                 expanded=o["outlook"] == "Upside risk" and o["confidence"] != "low"):
                    st.markdown(pick(o.get("summary_zh"), o.get("summary", "")))
                    drivers = o.get("drivers_zh") if is_zh() and o.get("drivers_zh") else o.get("drivers", [])
                    if drivers:
                        st.markdown(f"**{t('drivers')}：** " + "；".join(drivers) if is_zh()
                                    else f"**{t('drivers')}:** " + "; ".join(drivers))
                    st.markdown(f"**{t('watch_next')}：** " if is_zh() else f"**{t('watch_next')}:** ")
                    st.markdown(pick(o.get("watch_next_zh"), o.get("watch_next", "")))

        with st.expander(t("risk_method")):
            st.markdown(t("risk_method_text", days=OUTLOOK_DAYS,
                          cats="、".join(category_name(c) for c in sorted(SUPPLY_CATEGORIES)) if is_zh()
                          else ", ".join(sorted(SUPPLY_CATEGORIES))))

# ======================================================================
# HELP
# ======================================================================
with tab_help:
    st.markdown(t("help_md"))
