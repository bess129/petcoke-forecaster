"""
accuracy_tab.py - The "预测准确度 / Forecast Accuracy" page (supervisor point 1).
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from accuracy import metrics, rating, test_paths, track_record
from i18n import t, is_zh, name_of, model_name, fmt_month, fmt_price

RATING_COLOR = {"high": "#2e8b57", "mid": "#d4a017", "low": "#c0392b"}
RATING_ICON = {"high": "🟢", "mid": "🟡", "low": "🔴"}


def _pct(v, signed=False):
    if v is None or v != v:
        return "–"
    return f"{v:+.0%}" if signed else f"{v:.1%}"


def vs_text(m):
    if m["model"] == "No-change":
        return t("acc_vs_is_nc")
    v = m["vs_nochange"]
    if v != v:
        return "–"
    if v >= 0.03:
        return t("acc_vs_better", p=f"{v:.0%}")
    if v <= -0.03:
        return t("acc_vs_worse", p=f"{-v:.0%}")
    return t("acc_vs_same")


def render(available, forecast_for, histories):
    st.markdown(t("acc_intro"))
    rows, metric_of = [], {}
    for c in available:
        try:
            hist, fc, info = forecast_for(c)
            m = metrics(hist, info)
        except Exception:
            continue
        m["rating"] = rating(m["err"])
        metric_of[c] = (hist, info, m)
        rows.append({
            t("col_commodity"): name_of(c),
            t("acc_c_rating"): f"{RATING_ICON[m['rating']]} {t('rel_' + m['rating'])}",
            t("acc_c_all"): _pct(m["err"]),
            t("acc_c_1m"): _pct(m["err_1m"]),
            t("acc_c_3m"): _pct(m["err_3m"]),
            t("acc_c_vs"): vs_text(m),
            t("acc_c_dir"): t("acc_dir_none") if m["direction_hit"] != m["direction_hit"]
            else f"{m['direction_hit']:.0%}",
            t("acc_c_cov"): _pct(m["coverage"]),
            t("acc_c_worst"): f"{m['worst_err']:.0%}（{fmt_month(m['worst_month'])}）" if is_zh()
            else f"{m['worst_err']:.0%} ({fmt_month(m['worst_month'])})",
            t("acc_c_method"): model_name(m["model"]),
        })
    if not rows:
        st.info(t("acc_none"))
        return

    st.subheader(t("acc_table_title"))
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(t("acc_table_note"))

    # ------------------------------------------------------------ one commodity
    st.subheader(t("acc_detail_title"))
    choices = list(metric_of)
    c = st.selectbox(t("acc_pick"), choices, format_func=name_of, key="acc_pick")
    hist, info, m = metric_of[c]

    lo, hi = 100 * (1 - m["err_1m"]), 100 * (1 + m["err_1m"])
    st.markdown(t("acc_plain_1", n=m["n_tests"], err=_pct(m["err_1m"]),
                  lo=f"{lo:.0f}", hi=f"{hi:.0f}", worst=f"{m['worst_err']:.0%}",
                  when=fmt_month(m["worst_month"])))
    if m["model"] == "No-change" or (m["vs_nochange"] == m["vs_nochange"] and m["vs_nochange"] < 0.03):
        st.markdown(t("acc_plain_nc"))
    else:
        st.markdown(t("acc_plain_better", p=f"{m['vs_nochange']:.0%}"))
    if m["coverage"] == m["coverage"]:
        key = "acc_plain_cov_ok" if m["coverage"] >= 0.75 else "acc_plain_cov_low"
        st.markdown(t(key, cov=f"{m['coverage']:.0%}"))
    if c == "Green Pet Coke (GPC)":
        st.info(t("acc_gpc_news"))

    # chart: actual prices + what the system would have forecast at each test date
    start = m["first_test"] - pd.DateOffset(months=12)
    h = hist[hist["ds"] >= start]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=h["ds"], y=h["y"], mode="lines+markers", name=t("acc_actual"),
                             line=dict(color="#1f4e79", width=3)))
    for i, p in enumerate(test_paths(hist, info)):
        fig.add_trace(go.Scatter(x=p["ds"], y=p["y"], mode="lines", name=t("acc_past_fc"),
                                 legendgroup="fc", showlegend=i == 0,
                                 line=dict(color="#e67e22", width=1.6, dash="dot"),
                                 hovertemplate=t("acc_hover", when=fmt_month(p["origin"])) + " %{y:.2f}<extra></extra>"))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=-0.15), title=t("acc_chart_title"))
    st.plotly_chart(fig, width="stretch")

    bh = m["by_h"]
    bfig = go.Figure(go.Bar(x=[t("acc_months_ahead", n=int(k)) for k in bh.index], y=bh.values * 100,
                            marker_color="#7f8c8d", text=[f"{v:.1%}" for v in bh.values],
                            textposition="outside"))
    bfig.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10), title=t("acc_by_h_title"),
                       yaxis_title="%", yaxis_range=[0, float(bh.max()) * 140])
    st.plotly_chart(bfig, width="stretch")

    # ------------------------------------------------------------ live track record
    st.subheader(t("acc_live_title"))
    log = track_record(histories)
    if log.empty:
        st.info(t("acc_live_none"))
    else:
        done = log[log["status"] == "done"]
        if done.empty:
            st.info(t("acc_live_waiting", n=len(log)))
        else:
            st.markdown(t("acc_live_summary", n=len(done), err=_pct(done["error"].mean()),
                          nc=_pct(done["nochange_error"].mean()), cov=_pct(done["inside"].mean())))
        st.dataframe(pd.DataFrame({
            t("acc_l_made"): log["made_on"].dt.strftime("%Y-%m-%d"),
            t("col_commodity"): log["commodity"].map(name_of),
            t("acc_l_target"): log["target_month"].map(fmt_month),
            t("acc_l_fc"): [fmt_price(f, b) for f, b in zip(log["forecast"], log["base_price"])],
            t("acc_l_range"): [f"{fmt_price(a, b)} – {fmt_price(c_, b)}"
                               for a, c_, b in zip(log["lo"], log["hi"], log["base_price"])],
            t("acc_l_actual"): [fmt_price(a, b) if a == a else t("acc_l_wait")
                                for a, b in zip(log["actual"], log["base_price"])],
            t("acc_l_err"): ["–" if e != e else f"{e:.1%}" for e in log["error"]],
            t("acc_l_in"): ["–" if a != a else ("✓" if i else "✗")
                            for a, i in zip(log["actual"], log["inside"])],
        }), hide_index=True, width="stretch")
    st.caption(t("acc_live_note"))

    with st.expander(t("acc_how_title")):
        st.markdown(t("acc_how_md", n=m["n_tests"]))
