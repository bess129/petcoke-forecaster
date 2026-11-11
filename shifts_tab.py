"""
shifts_tab.py - The "价格大变动与事件 / Price Shifts & Events" page (supervisor point 2).
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from shifts import load_events, patterns, current_watch, EVENTS_JSON, WATCH_ALERT
from shock import signals_for
from i18n import t, is_zh, pick, name_of, unit_of, category_name, fmt_month, fmt_price, zh_punct

UP, DOWN = "#1f77b4", "#7f7f7f"          # neutral colours (in China red = up)


@st.cache_data(ttl=1800, show_spinner=False)
def _events(_mtime):
    return load_events()


def _lead_text(days):
    if days is None or days != days:
        return "–"
    days = int(days)
    if days <= 0:
        return t("sh_lead_same")
    return t("sh_lead_m", m=f"{days / 30:.1f}") if days >= 45 else t("sh_lead_d", d=days)


def _headline(h):
    title = pick(h.get("title_zh"), h.get("title", ""))
    extra = f" · {_lead_text(h['lead_days'])}" if "lead_days" in h else ""
    return f"- {h['date']} · [{title}]({h['link']}) <span style='color:#888'>({h['source']}{extra})</span>"


def render(available, histories, signals):
    mtime = EVENTS_JSON.stat().st_mtime if EVENTS_JSON.exists() else 0
    events = _events(mtime)
    shifts = events.get("shifts", {})
    st.markdown(t("sh_intro"))
    if not shifts:
        st.info(t("sh_none"))
        return

    # ------------------------------------------------------------ early-warning check (now)
    st.subheader(t("sh_watch_title"))
    st.caption(t("sh_watch_caption"))
    rows, alerts = [], []
    sep = "、" if is_zh() else ", "
    for c in available:
        watch = current_watch(events, signals, c, signals_for)
        if not watch:
            continue
        top = sorted(watch, key=lambda w: -w["past_moves"])[:3]
        found = sorted((w for w in watch if w["now"]), key=lambda w: -w["now"])
        now_total = sum(w["now"] for w in watch)
        alert = any(w["now"] >= WATCH_ALERT for w in watch)
        leads = [w["median_lead"] for w in top if w["median_lead"] == w["median_lead"]]
        rows.append({
            t("col_commodity"): name_of(c),
            t("sh_c_past"): sep.join(f"{category_name(w['category'])}" + (f"（{w['past_moves']}）" if is_zh()
                                     else f" ({w['past_moves']})") for w in top),
            t("sh_c_lead"): _lead_text(np.median(leads) if leads else np.nan),
            t("sh_c_now"): sep.join(t("sh_n_items", cat=category_name(w["category"]), n=w["now"]) for w in found)
            or "–",
            t("sh_c_status"): t("sh_alert") if alert else (t("sh_some") if now_total else t("sh_quiet")),
            "_sort": (2 if alert else 1 if now_total else 0, now_total),
        })
        if alert:
            alerts.append((c, [w for w in found if w["now"] >= WATCH_ALERT]))
    if rows:
        df = pd.DataFrame(rows).sort_values("_sort", ascending=False).drop(columns="_sort")
        st.dataframe(df, hide_index=True, width="stretch")
        for c, ws in alerts:
            with st.expander(t("sh_alert_detail", name=name_of(c))):
                for w in ws:
                    st.markdown(f"**{category_name(w['category'])}**")
                    st.markdown("\n".join(
                        f"- {r['published_date']:%Y-%m-%d} · [{pick(r.get('headline_zh'), r['title'])}]({r['link']})"
                        for _, r in w["examples"].iterrows()))
    else:
        st.info(t("sh_watch_none"))

    # ------------------------------------------------------------ patterns
    pat = patterns(events)
    if not pat.empty:
        st.subheader(t("sh_pat_title"))
        pt = pat.copy()
        st.dataframe(pd.DataFrame({
            t("sh_c_cat"): pt["category"].map(category_name),
            t("sh_c_dir"): pt["direction"].map(lambda d: t("sh_up") if d == "up" else t("sh_down")),
            t("sh_c_moves"): pt["moves"],
            t("sh_c_warned"): pt["warned_share"].map(lambda v: f"{v:.0%}"),
            t("sh_c_lead"): pt["median_lead"].map(_lead_text),
        }), hide_index=True, width="stretch")
        st.caption(t("sh_pat_note"))

    # ------------------------------------------------------------ one commodity timeline
    st.subheader(t("sh_tl_title"))
    with_shifts = [c for c in available if any(e["commodity"] == c for e in shifts.values())]
    if not with_shifts:
        return
    c = st.selectbox(t("acc_pick"), with_shifts, format_func=name_of, key="sh_pick")
    hist = histories[c]
    mine = sorted((e for e in shifts.values() if e["commodity"] == c), key=lambda e: e["end"])

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist["ds"], y=hist["y"], mode="lines", name=t("acc_actual"),
                             line=dict(color="#1f4e79", width=2.5)))
    for i, e in enumerate(mine, start=1):
        s, en = pd.Timestamp(e["start"]), pd.Timestamp(e["end"])
        fig.add_vrect(x0=s, x1=en, fillcolor="#f39c12" if e["direction"] == "up" else "#95a5a6",
                      opacity=0.18, line_width=0)
        fig.add_annotation(x=en, y=e["price_end"], text=f"{i}. {e['change']:+.0%}", showarrow=True,
                           arrowhead=2, ax=0, ay=-30, font=dict(size=12))
        for w in e.get("warnings", []):
            fig.add_trace(go.Scatter(
                x=[pd.Timestamp(w["date"])], y=[float(hist.set_index("ds")["y"].asof(pd.Timestamp(w["date"])))],
                mode="markers", marker=dict(symbol="diamond", size=10, color="#e67e22"),
                name=t("sh_warn_marker"), legendgroup="w", showlegend=False,
                hovertext=pick(w.get("title_zh"), w["title"]), hoverinfo="text"))
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=t("sh_warn_marker"),
                             marker=dict(symbol="diamond", size=10, color="#e67e22")))
    fig.update_layout(height=400, margin=dict(l=10, r=10, t=30, b=10), yaxis_title=unit_of(c),
                      legend=dict(orientation="h", y=-0.12), title=t("sh_chart_title"))
    st.plotly_chart(fig, width="stretch")

    for i, e in enumerate(reversed(mine), start=0):
        n = len(mine) - i
        arrow = "▲" if e["direction"] == "up" else "▼"
        head = (f"{n}. {fmt_month(pd.Timestamp(e['start']))} → {fmt_month(pd.Timestamp(e['end']))}　"
                f"{arrow} {e['change']:+.0%}　{pick(e.get('cause_zh'), e.get('cause_en', ''))}")
        with st.expander(head, expanded=i == 0):
            st.markdown(f"**{t('sh_price')}**：{fmt_price(e['price_start'])} → {fmt_price(e['price_end'])} {unit_of(c)}"
                        if is_zh() else f"**{t('sh_price')}**: {fmt_price(e['price_start'])} → "
                        f"{fmt_price(e['price_end'])} {unit_of(c)}")
            if e.get("categories"):
                sep = "、" if is_zh() else ", "
                st.markdown(f"**{t('sh_types')}**{'：' if is_zh() else ': '}"
                            + sep.join(category_name(x) for x in e["categories"]))
            expl = pick(e.get("explanation_zh"), e.get("explanation_en", ""))
            if expl:
                st.markdown(expl)
            if e.get("warnings"):
                st.markdown(f"**{t('sh_warnings')}**")
                st.markdown("\n".join(_headline(w) for w in e["warnings"]), unsafe_allow_html=True)
            else:
                st.markdown(f"**{t('sh_warnings')}** {t('sh_no_warn')}")
            if e.get("drivers"):
                st.markdown(f"**{t('sh_drivers')}**")
                st.markdown("\n".join(_headline(d) for d in e["drivers"]), unsafe_allow_html=True)
            lesson = pick(e.get("lesson_zh"), e.get("lesson_en", ""))
            if lesson:
                st.markdown(f"💡 **{t('sh_lesson')}**{'：' if is_zh() else ': '}{lesson}")
            st.caption(t("sh_conf", conf=t("conf_" + e.get("confidence", "low")), n=e.get("n_headlines", 0)))

    with st.expander(t("sh_how_title")):
        st.markdown(t("sh_how_md"))
