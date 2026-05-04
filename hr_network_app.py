"""
인력 네트워크 분석 시스템
3개 데이터 소스: 피어리뷰 · 메시지 플랫폼 · 행사 참여 리더기
"""
from __future__ import annotations

import warnings
from collections import Counter
from itertools import combinations

import networkx as nx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from src.network_analyzer import (
    COLLAB_FREQUENCIES,
    LETTER_TYPES,
    ALL_KEYWORDS,
    GroupingConfig,
    MessageRecord,
    NetworkAnalysisResult,
    build_centrality_bar_df,
    build_network,
    build_pyvis_html,
    compute_cross_analysis,
    detect_column_mapping,
    generate_sample_attendance,
    generate_sample_messages,
    generate_sample_peerreview,
    group_by_time_window,
    parse_attendance_dataframe,
    parse_message_dataframe,
    parse_peerreview_dataframe,
    run_analysis,
)

warnings.filterwarnings("ignore")

st.set_page_config(
    page_title="인력 네트워크 분석",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.block-title {font-size:1.15rem; font-weight:700; color:#1f3864; margin-bottom:4px;}
.hyp-box {background:#fff8e7; padding:10px 14px; border-left:4px solid #f0a500;
           border-radius:4px; margin-bottom:12px; font-size:.92rem;}
.insight-box {background:#e8f5e9; padding:10px 14px; border-left:4px solid #2e7d32;
              border-radius:4px; margin:6px 0; font-size:.92rem;}
</style>
""", unsafe_allow_html=True)

# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _read_file(uploaded) -> pd.DataFrame:
    return pd.read_csv(uploaded) if uploaded.name.lower().endswith(".csv") else pd.read_excel(uploaded)


def _col_selector(label: str, key: str, cols: list[str], auto: dict, sidebar=True) -> str:
    opts = ["(없음)"] + cols
    default = auto.get(key, "(없음)")
    idx = opts.index(default) if default in opts else 0
    fn = st.sidebar.selectbox if sidebar else st.selectbox
    return fn(label, opts, index=idx, key=key)


def _build_message_network(records: list[MessageRecord]) -> nx.DiGraph:
    G: nx.DiGraph = nx.DiGraph()
    for r in records:
        u, v = r.sender_id, r.receiver_id
        if not G.has_edge(u, v):
            G.add_edge(u, v, weight=0, count=0)
            G.nodes[u].setdefault("department", r.sender_dept)
            G.nodes[v].setdefault("department", r.receiver_dept)
        G[u][v]["weight"] += 1
        G[u][v]["count"] += 1
    return G


def _pyvis_digraph(G: nx.DiGraph, height=600) -> str:
    """Simple directed pyvis graph for message / peer-review networks."""
    from pyvis.network import Network

    dept_list = sorted({G.nodes[n].get("department", "미분류") for n in G.nodes()})
    palette = [
        "#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6",
        "#1abc9c","#e67e22","#34495e","#e91e63","#00bcd4",
        "#8bc34a","#ff5722","#607d8b","#795548","#ffeb3b",
    ]
    dept_color = {d: palette[i % len(palette)] for i, d in enumerate(dept_list)}

    net = Network(height=f"{height}px", width="100%", bgcolor="#1a1a2e",
                  font_color="#fff", directed=True, notebook=False)
    net.set_options("""{"physics":{"forceAtlas2Based":{"gravitationalConstant":-50,
      "springLength":120},"solver":"forceAtlas2Based",
      "stabilization":{"iterations":120}},
      "edges":{"smooth":{"type":"continuous"},"arrows":{"to":{"enabled":true,"scaleFactor":0.6}}},
      "interaction":{"hover":true,"navigationButtons":true}}""")

    deg = dict(G.degree())
    max_deg = max(deg.values(), default=1)
    for n in G.nodes():
        dept = G.nodes[n].get("department", "미분류")
        size = 10 + (deg.get(n, 0) / max_deg) * 40
        net.add_node(n, label=str(n), title=f"{n}<br>부서:{dept}<br>연결:{deg[n]}",
                     color=dept_color.get(dept, "#888"), size=size)

    max_w = max((d["weight"] for _, _, d in G.edges(data=True)), default=1)
    for u, v, d in G.edges(data=True):
        w = d["weight"]
        net.add_edge(u, v, width=1 + (w / max_w) * 6,
                     title=f"발송 {d['count']}회", color="#aaaaaa60")
    return net.generate_html()


def _dept_heatmap(df: pd.DataFrame, from_col: str, to_col: str, title: str) -> go.Figure:
    ct = pd.crosstab(df[from_col], df[to_col])
    fig = px.imshow(ct, text_auto=True, color_continuous_scale="Blues",
                    title=title, aspect="auto")
    fig.update_layout(height=420, xaxis_title="수신 부서", yaxis_title="발신 부서")
    return fig


# ────────────────────────────────────────────────────────────────────
# Sidebar – data loading
# ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔗 인력 네트워크")
    st.caption("피어리뷰 · 메시지 · 행사 참여 통합 분석")
    st.divider()

    use_sample = st.button("📦 샘플 데이터 사용", use_container_width=True)
    st.divider()
    st.subheader("📂 파일 업로드")
    peer_file = st.file_uploader("① 피어리뷰 데이터", type=["csv","xlsx","xls"], key="peer_file")
    msg_file  = st.file_uploader("② 메시지 플랫폼 데이터", type=["csv","xlsx","xls"], key="msg_file")
    att_file  = st.file_uploader("③ 행사 참여 데이터", type=["csv","xlsx","xls"], key="att_file")

if use_sample:
    att_df  = generate_sample_attendance()
    peer_df = generate_sample_peerreview(att_df)
    msg_df  = generate_sample_messages(att_df)
    st.session_state.update({"att_df": att_df, "peer_df": peer_df, "msg_df": msg_df, "result": None})
    st.sidebar.success("샘플 데이터 로드 완료!")

if att_file:
    st.session_state["att_df"] = _read_file(att_file); st.session_state["result"] = None
if peer_file:
    st.session_state["peer_df"] = _read_file(peer_file); st.session_state["result"] = None
if msg_file:
    st.session_state["msg_df"] = _read_file(msg_file); st.session_state["result"] = None

att_df:  pd.DataFrame | None = st.session_state.get("att_df")
peer_df: pd.DataFrame | None = st.session_state.get("peer_df")
msg_df:  pd.DataFrame | None = st.session_state.get("msg_df")

# ── Column mapping UI ────────────────────────────────────────────────
att_col_map = peer_col_map = msg_col_map = {}

if att_df is not None:
    with st.sidebar:
        st.divider(); st.subheader("🗂 행사 데이터 컬럼")
        auto = detect_column_mapping(att_df, "attendance")
        cols = list(att_df.columns)
        att_col_map = {
            "employee_id": _col_selector("사번 *",    "att_employee_id", cols, auto),
            "name":        _col_selector("이름",      "att_name",        cols, auto),
            "event_id":    _col_selector("행사명 *",  "att_event_id",    cols, auto),
            "tag_time":    _col_selector("태깅시간 *","att_tag_time",    cols, auto),
            "department":  _col_selector("부서",      "att_department",  cols, auto),
        }
        att_col_map = {k: v for k, v in att_col_map.items() if v != "(없음)"}

if peer_df is not None:
    with st.sidebar:
        st.divider(); st.subheader("🗂 피어리뷰 컬럼")
        auto = detect_column_mapping(peer_df, "peerreview")
        cols = list(peer_df.columns)
        peer_col_map = {
            "requester_id":      _col_selector("리뷰대상자 사번 *",  "pr_requester_id",      cols, auto),
            "requester_dept":    _col_selector("리뷰대상자 부서",    "pr_requester_dept",    cols, auto),
            "reviewer_id":       _col_selector("리뷰어 사번 *",      "pr_reviewer_id",       cols, auto),
            "reviewer_dept":     _col_selector("리뷰어 부서",        "pr_reviewer_dept",     cols, auto),
            "collab_frequency":  _col_selector("협업 빈도",          "pr_collab_frequency",  cols, auto),
            "assignment_method": _col_selector("배정 방식",          "pr_assignment_method", cols, auto),
        }
        peer_col_map = {k: v for k, v in peer_col_map.items() if v != "(없음)"}

if msg_df is not None:
    with st.sidebar:
        st.divider(); st.subheader("🗂 메시지 플랫폼 컬럼")
        auto = detect_column_mapping(msg_df, "message")
        cols = list(msg_df.columns)
        msg_col_map = {
            "sender_id":    _col_selector("발신자 ID *",   "msg_sender_id",    cols, auto),
            "receiver_id":  _col_selector("수신자 ID *",   "msg_receiver_id",  cols, auto),
            "sender_dept":  _col_selector("발신자 부서",   "msg_sender_dept",  cols, auto),
            "receiver_dept":_col_selector("수신자 부서",   "msg_receiver_dept",cols, auto),
            "send_dt":      _col_selector("발송일시",      "msg_send_dt",      cols, auto),
            "letter_type":  _col_selector("레터 유형",     "msg_letter_type",  cols, auto),
            "keywords":     _col_selector("키워드",        "msg_keywords",     cols, auto),
            "is_anonymous": _col_selector("익명 여부",     "msg_is_anonymous", cols, auto),
        }
        msg_col_map = {k: v for k, v in msg_col_map.items() if v != "(없음)"}

# ── Analysis params + Run ────────────────────────────────────────────
with st.sidebar:
    st.divider(); st.subheader("⚙️ 분석 파라미터")
    time_window = st.slider("행사 그룹화 시간 임계값 (초)", 1, 120, 120,
                            help="이 초 이내에 연속 태깅 → 같은 그룹")
    top_n = st.slider("Top N 표시", 5, 30, 15)
    run_btn = st.button("🚀 분석 실행", type="primary", use_container_width=True)

# ────────────────────────────────────────────────────────────────────
# Run analysis
# ────────────────────────────────────────────────────────────────────

if run_btn:
    if att_df is None and peer_df is None and msg_df is None:
        st.sidebar.error("데이터를 먼저 로드하세요.")
    else:
        with st.spinner("분석 중..."):
            warns: list[str] = []

            # -- Event network
            att_records, aw = [], []
            event_groups: dict = {}
            if att_df is not None and att_col_map:
                try:
                    att_records, aw = parse_attendance_dataframe(att_df, att_col_map)
                    warns += aw
                    event_groups = group_by_time_window(att_records, GroupingConfig(time_window))
                except ValueError as e:
                    st.error(str(e))

            # -- Peer review records
            peer_records, pw = [], []
            if peer_df is not None and peer_col_map:
                try:
                    peer_records, pw = parse_peerreview_dataframe(peer_df, peer_col_map)
                    warns += pw
                except ValueError as e:
                    st.error(str(e))

            # -- Message records
            msg_records, mw = [], []
            if msg_df is not None and msg_col_map:
                try:
                    msg_records, mw = parse_message_dataframe(msg_df, msg_col_map)
                    warns += mw
                except ValueError as e:
                    st.error(str(e))

            for w in warns[:10]:
                st.warning(w)

            # -- Build combined graph (event + peer review)
            G_combined = build_network(event_groups, peer_records if peer_records else None)
            result = run_analysis(G_combined) if G_combined.number_of_nodes() > 0 else None

            # -- Message graph
            msg_G = _build_message_network(msg_records) if msg_records else nx.DiGraph()

            # -- Cross analysis
            cross = compute_cross_analysis(event_groups, peer_records) if event_groups and peer_records else None

            st.session_state.update({
                "result": result,
                "event_groups": event_groups,
                "peer_records": peer_records,
                "msg_records": msg_records,
                "msg_G": msg_G,
                "cross": cross,
                "top_n": top_n,
            })
            st.sidebar.success("분석 완료!")

# ────────────────────────────────────────────────────────────────────
# Welcome screen
# ────────────────────────────────────────────────────────────────────

if att_df is None and peer_df is None and msg_df is None:
    st.title("🔗 인력 네트워크 분석 시스템")
    st.markdown("**피어리뷰 · 메시지 플랫폼 · 행사 참여** 3개 데이터 소스를 통합 분석합니다.")
    c1, c2, c3 = st.columns(3)
    c1.info("**① 피어리뷰**\n- 리뷰대상자/리뷰어 사번\n- 협업 빈도\n- 배정방식")
    c2.info("**② 메시지 플랫폼**\n- 발신자/수신자 ID\n- 레터유형·키워드\n- 발송일시·익명여부")
    c3.info("**③ 행사 참여**\n- 출입일시 (태깅)\n- 사번·부서·행사명")
    st.stop()

# ────────────────────────────────────────────────────────────────────
# Tabs
# ────────────────────────────────────────────────────────────────────

tab_overview, tab_peer, tab_msg, tab_event, tab_combined, tab_hyp = st.tabs([
    "📊 전체 현황",
    "👥 피어리뷰 네트워크",
    "💬 메시지 네트워크",
    "🏢 행사 참여 네트워크",
    "🔗 통합 네트워크",
    "🔬 가설 검증",
])

# ══════════════════════════════════════════════════════════════════════
# TAB 1 – Overview
# ══════════════════════════════════════════════════════════════════════
with tab_overview:
    st.header("📊 전체 데이터 현황")
    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown('<p class="block-title">📋 피어리뷰</p>', unsafe_allow_html=True)
        if peer_df is not None:
            rq_col = peer_col_map.get("requester_id", "")
            rv_col = peer_col_map.get("reviewer_id", "")
            freq_col = peer_col_map.get("collab_frequency", "")
            st.metric("총 추천 건수", len(peer_df))
            if rq_col and rq_col in peer_df.columns:
                st.metric("리뷰 대상자 수", peer_df[rq_col].nunique())
            if rv_col and rv_col in peer_df.columns:
                st.metric("리뷰어 수", peer_df[rv_col].nunique())
            if freq_col and freq_col in peer_df.columns:
                fd = peer_df[freq_col].value_counts()
                fig = px.pie(values=fd.values, names=fd.index, title="협업 빈도 분포",
                             color_discrete_sequence=px.colors.qualitative.Set3,
                             category_orders={"names": COLLAB_FREQUENCIES})
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("피어리뷰 데이터 없음")

    with c2:
        st.markdown('<p class="block-title">💬 메시지 플랫폼</p>', unsafe_allow_html=True)
        if msg_df is not None:
            s_col  = msg_col_map.get("sender_id", "")
            anon_col = msg_col_map.get("is_anonymous", "")
            lt_col = msg_col_map.get("letter_type", "")
            st.metric("총 메시지 수", len(msg_df))
            if s_col and s_col in msg_df.columns:
                st.metric("발신자 수", msg_df[s_col].nunique())
            if anon_col and anon_col in msg_df.columns:
                anon_n = (msg_df[anon_col].astype(str).str.upper().isin(["Y","YES","TRUE","1","익명"])).sum()
                st.metric("익명 메시지", anon_n)
            if lt_col and lt_col in msg_df.columns:
                ltd = msg_df[lt_col].value_counts()
                fig2 = px.pie(values=ltd.values, names=ltd.index, title="레터 유형 분포",
                              color_discrete_sequence=px.colors.qualitative.Pastel)
                st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("메시지 데이터 없음")

    with c3:
        st.markdown('<p class="block-title">🏢 행사 참여</p>', unsafe_allow_html=True)
        if att_df is not None:
            ev_col = att_col_map.get("event_id", "")
            st.metric("총 태깅 기록", len(att_df))
            if ev_col and ev_col in att_df.columns:
                st.metric("행사 종류", att_df[ev_col].nunique())
                ev_dist = att_df[ev_col].value_counts()
                fig3 = px.bar(x=ev_dist.values, y=ev_dist.index, orientation="h",
                              title="행사별 참여 기록 수", color=ev_dist.values,
                              color_continuous_scale="Blues",
                              labels={"x":"태깅 수","y":"행사명"})
                st.plotly_chart(fig3, use_container_width=True)
        else:
            st.info("행사 데이터 없음")

    # Department cross-tabs
    st.divider(); st.subheader("🏗 부서별 요약")
    d1, d2 = st.columns(2)
    with d1:
        rq_d = peer_col_map.get("requester_dept", "")
        if peer_df is not None and rq_d and rq_d in peer_df.columns:
            dd = peer_df[rq_d].value_counts().reset_index(names="부서")
            dd.columns = ["부서", "건수"]
            fig4 = px.bar(dd, x="부서", y="건수", title="부서별 피어리뷰 건수",
                          color="부서", color_discrete_sequence=px.colors.qualitative.Set2)
            fig4.update_xaxes(tickangle=30); st.plotly_chart(fig4, use_container_width=True)
    with d2:
        s_d = msg_col_map.get("sender_dept", "")
        if msg_df is not None and s_d and s_d in msg_df.columns:
            dd2 = msg_df[s_d].value_counts().reset_index(names="부서")
            dd2.columns = ["부서", "건수"]
            fig5 = px.bar(dd2, x="부서", y="건수", title="부서별 메시지 발신 수",
                          color="부서", color_discrete_sequence=px.colors.qualitative.Set2)
            fig5.update_xaxes(tickangle=30); st.plotly_chart(fig5, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════
# TAB 2 – Peer Review Network
# ══════════════════════════════════════════════════════════════════════
with tab_peer:
    st.header("👥 피어리뷰 네트워크 분석")
    result: NetworkAnalysisResult | None = st.session_state.get("result")
    peer_records = st.session_state.get("peer_records", [])

    if not peer_records:
        st.info("피어리뷰 데이터를 로드하고 분석을 실행하세요.")
    else:
        # Build directed peer-review graph
        pr_G: nx.DiGraph = nx.DiGraph()
        for pr in peer_records:
            if not pr_G.has_edge(pr.requester_id, pr.reviewer_id):
                pr_G.add_edge(pr.requester_id, pr.reviewer_id,
                              weight=0, count=0, freq=pr.collab_frequency, method=pr.assignment_method)
                pr_G.nodes[pr.requester_id].setdefault("department", pr.requester_dept)
                pr_G.nodes[pr.reviewer_id].setdefault("department", pr.reviewer_dept)
            pr_G[pr.requester_id][pr.reviewer_id]["count"] += 1

        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("**네트워크 그래프** — 화살표 방향: 리뷰대상자 → 리뷰어 (추천한 방향)")
            html_pr = _pyvis_digraph(pr_G, height=580)
            components.html(html_pr, height=600, scrolling=False)

        with c2:
            st.markdown("**리뷰어로 많이 선정된 직원 TOP**")
            in_deg = dict(pr_G.in_degree())
            top_rv = sorted(in_deg.items(), key=lambda x: x[1], reverse=True)[:top_n]
            df_top = pd.DataFrame(top_rv, columns=["사번","선정횟수"])
            dept_map = {pr.reviewer_id: pr.reviewer_dept for pr in peer_records}
            df_top["부서"] = df_top["사번"].map(dept_map)
            fig_rv = px.bar(df_top.head(12), x="선정횟수", y="사번", orientation="h",
                            color="부서", title="리뷰어 선정 횟수 TOP")
            fig_rv.update_layout(yaxis={"categoryorder":"total ascending"})
            st.plotly_chart(fig_rv, use_container_width=True)

            # same vs cross dept
            same = sum(1 for pr in peer_records if pr.requester_dept == pr.reviewer_dept)
            cross_d = len(peer_records) - same
            fig_dc = px.pie(values=[same, cross_d], names=["같은 부서","다른 부서"],
                            title="같은/다른 부서 추천 비율",
                            color_discrete_sequence=["#4472C4","#ED7D31"])
            st.plotly_chart(fig_dc, use_container_width=True)

        st.divider()
        st.subheader("🗺 부서 간 피어리뷰 히트맵")
        pr_flat = pd.DataFrame([{
            "발신자_부서": r.requester_dept, "수신자_부서": r.reviewer_dept
        } for r in peer_records])
        if not pr_flat.empty:
            st.plotly_chart(
                _dept_heatmap(pr_flat, "발신자_부서", "수신자_부서", "부서 간 피어리뷰 추천 현황"),
                use_container_width=True,
            )

        st.divider()
        st.subheader("📊 협업 빈도 분포 분석")
        if peer_records[0].collab_frequency:
            freq_df = pd.DataFrame([{
                "협업빈도": r.collab_frequency,
                "같은부서": "같은 부서" if r.requester_dept == r.reviewer_dept else "다른 부서",
                "배정방식": r.assignment_method,
            } for r in peer_records if r.collab_frequency])

            fa, fb = st.columns(2)
            with fa:
                fig_f = px.histogram(freq_df, x="협업빈도", color="같은부서", barmode="group",
                                     title="부서 구분별 협업 빈도",
                                     category_orders={"협업빈도": COLLAB_FREQUENCIES},
                                     color_discrete_sequence=["#4472C4","#ED7D31"])
                fig_f.update_xaxes(tickangle=20)
                st.plotly_chart(fig_f, use_container_width=True)
            with fb:
                if freq_df["배정방식"].str.strip().any():
                    fig_m = px.histogram(freq_df, x="배정방식", color="협업빈도", barmode="stack",
                                         title="배정방식별 협업 빈도 분포",
                                         category_orders={"협업빈도": COLLAB_FREQUENCIES})
                    st.plotly_chart(fig_m, use_container_width=True)

        st.divider()
        st.subheader("🔄 상호 추천 분석")
        mutual = [(u, v) for u, v in pr_G.edges() if pr_G.has_edge(v, u) and u < v]
        st.metric("상호 추천 쌍 수", len(mutual))
        if mutual:
            mut_rows = [{
                "직원A": u, "A 부서": pr_G.nodes[u].get("department",""),
                "직원B": v, "B 부서": pr_G.nodes[v].get("department",""),
                "A→B 빈도": pr_G[u][v].get("freq",""),
                "B→A 빈도": pr_G[v][u].get("freq",""),
            } for u, v in mutual]
            st.dataframe(pd.DataFrame(mut_rows), use_container_width=True, height=250)

# ══════════════════════════════════════════════════════════════════════
# TAB 3 – Message Network
# ══════════════════════════════════════════════════════════════════════
with tab_msg:
    st.header("💬 메시지 플랫폼 네트워크 분석")
    msg_records: list[MessageRecord] = st.session_state.get("msg_records", [])
    msg_G_stored: nx.DiGraph = st.session_state.get("msg_G", nx.DiGraph())

    if not msg_records:
        st.info("메시지 데이터를 로드하고 분석을 실행하세요.")
    else:
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("**메시지 네트워크** — 엣지 두께 = 발송 횟수, 화살표 = 발신 방향")
            components.html(_pyvis_digraph(msg_G_stored, 580), height=600, scrolling=False)

        with c2:
            out_d = dict(msg_G_stored.out_degree(weight="weight"))
            in_d  = dict(msg_G_stored.in_degree(weight="weight"))
            top_send = sorted(out_d.items(), key=lambda x: x[1], reverse=True)[:10]
            top_recv = sorted(in_d.items(),  key=lambda x: x[1], reverse=True)[:10]

            fig_s = px.bar(pd.DataFrame(top_send, columns=["ID","발신"]),
                           x="발신", y="ID", orientation="h", title="발신 TOP 10",
                           color="발신", color_continuous_scale="Blues")
            fig_s.update_layout(yaxis={"categoryorder":"total ascending"})
            st.plotly_chart(fig_s, use_container_width=True)

            fig_r = px.bar(pd.DataFrame(top_recv, columns=["ID","수신"]),
                           x="수신", y="ID", orientation="h", title="수신 TOP 10",
                           color="수신", color_continuous_scale="Reds")
            fig_r.update_layout(yaxis={"categoryorder":"total ascending"})
            st.plotly_chart(fig_r, use_container_width=True)

        st.divider()

        # keyword analysis
        st.subheader("🏷 키워드 분석")
        all_kw = Counter(kw for r in msg_records for kw in r.keywords)
        ka, kb = st.columns(2)
        with ka:
            df_kw = pd.DataFrame(all_kw.most_common(20), columns=["키워드","빈도"])
            fig_kw = px.bar(df_kw, x="키워드", y="빈도", title="전체 키워드 빈도",
                            color="빈도", color_continuous_scale="Viridis")
            st.plotly_chart(fig_kw, use_container_width=True)
        with kb:
            kw_type = [{"키워드": kw, "레터유형": r.letter_type}
                       for r in msg_records for kw in r.keywords if r.letter_type]
            if kw_type:
                df_kt = pd.DataFrame(kw_type)
                kt_pivot = df_kt.groupby(["레터유형","키워드"]).size().reset_index(name="수")
                fig_kt = px.bar(kt_pivot, x="레터유형", y="수", color="키워드",
                                title="레터 유형별 키워드 분포")
                st.plotly_chart(fig_kt, use_container_width=True)

        st.divider()
        st.subheader("📅 시간대 분석")
        dt_records = [r for r in msg_records if pd.notna(r.send_dt)]
        if dt_records:
            dt_df = pd.DataFrame({
                "시간대": [r.send_dt.hour for r in dt_records],
                "날짜": [r.send_dt.date() for r in dt_records],
                "레터유형": [r.letter_type for r in dt_records],
            })
            ta, tb = st.columns(2)
            with ta:
                hd = dt_df["시간대"].value_counts().sort_index().reset_index()
                hd.columns = ["시간대","건수"]
                fig_h = px.bar(hd, x="시간대", y="건수", title="시간대별 메시지 발송",
                               color="건수", color_continuous_scale="Blues")
                st.plotly_chart(fig_h, use_container_width=True)
            with tb:
                dd = dt_df.groupby("날짜").size().reset_index(name="건수")
                fig_d = px.line(dd, x="날짜", y="건수", title="일별 메시지 트렌드")
                st.plotly_chart(fig_d, use_container_width=True)

        st.divider()
        st.subheader("🗺 부서 간 메시지 히트맵")
        msg_dept_df = pd.DataFrame({
            "발신자_부서": [r.sender_dept for r in msg_records],
            "수신자_부서": [r.receiver_dept for r in msg_records],
        })
        st.plotly_chart(
            _dept_heatmap(msg_dept_df, "발신자_부서", "수신자_부서", "부서 간 메시지 발송 현황"),
            use_container_width=True,
        )

        st.divider()
        st.subheader("🕵️ 익명 메시지 분석")
        anon_n  = sum(1 for r in msg_records if r.is_anonymous)
        total_n = len(msg_records)
        ea, eb = st.columns(2)
        with ea:
            st.metric("익명 메시지 비율", f"{anon_n/total_n*100:.1f}%")
            fig_an = px.pie(values=[anon_n, total_n - anon_n], names=["익명","실명"],
                            title="익명/실명 비율",
                            color_discrete_sequence=["#FF6B6B","#4ECDC4"])
            st.plotly_chart(fig_an, use_container_width=True)
        with eb:
            from collections import defaultdict
            dept_anon: dict[str, list] = defaultdict(list)
            for r in msg_records:
                dept_anon[r.sender_dept].append(r.is_anonymous)
            anon_rate_df = pd.DataFrame([
                {"부서": d, "익명률(%)": sum(vs)/len(vs)*100}
                for d, vs in dept_anon.items()
            ]).sort_values("익명률(%)")
            fig_ad = px.bar(anon_rate_df, x="익명률(%)", y="부서", orientation="h",
                            title="부서별 익명 메시지 비율",
                            color="익명률(%)", color_continuous_scale="Reds")
            st.plotly_chart(fig_ad, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════
# TAB 4 – Event Network
# ══════════════════════════════════════════════════════════════════════
with tab_event:
    st.header("🏢 행사 참여 네트워크 분석")
    result = st.session_state.get("result")
    event_groups = st.session_state.get("event_groups", {})

    if result is None or not event_groups:
        st.info("행사 데이터를 로드하고 분석을 실행하세요.")
    else:
        # Build event-only graph
        ev_G: nx.Graph = nx.Graph()
        for ev, groups in event_groups.items():
            for grp in groups:
                if len(grp) < 2:
                    continue
                for a, b in combinations(grp, 2):
                    if not ev_G.has_edge(a.employee_id, b.employee_id):
                        ev_G.add_edge(a.employee_id, b.employee_id, weight=0, co_events=0)
                        ev_G.nodes[a.employee_id].setdefault("department", a.department)
                        ev_G.nodes[b.employee_id].setdefault("department", b.department)
                    ev_G[a.employee_id][b.employee_id]["weight"] += 1
                    ev_G[a.employee_id][b.employee_id]["co_events"] += 1

        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("**행사 공동 참여 네트워크** — 엣지 두께 = 공동 참여 횟수")
            # Use existing build_pyvis_html with event-only result
            ev_result = run_analysis(ev_G)
            html_ev = build_pyvis_html(ev_G, ev_result, color_by="department")
            components.html(html_ev, height=600, scrolling=False)

        with c2:
            # Group stats
            all_sizes = [len(g) for grps in event_groups.values() for g in grps]
            st.metric("감지된 그룹 수", len(all_sizes))
            st.metric("평균 그룹 크기", f"{sum(all_sizes)/max(len(all_sizes),1):.1f}명")
            st.metric("단독 참여", sum(1 for s in all_sizes if s == 1))
            st.metric("그룹 참여(2인+)", sum(1 for s in all_sizes if s > 1))

            sz_cnt = Counter(all_sizes)
            fig_sz = px.bar(x=list(sz_cnt.keys()), y=list(sz_cnt.values()),
                            title="그룹 크기 분포",
                            labels={"x":"그룹 크기","y":"빈도"})
            st.plotly_chart(fig_sz, use_container_width=True)

        st.divider()
        # co-event heatmap by department
        st.subheader("부서 간 행사 공동 참여 현황")
        ev_pairs_df = pd.DataFrame([{
            "발신자_부서": ev_G.nodes[u].get("department",""),
            "수신자_부서": ev_G.nodes[v].get("department",""),
        } for u, v in ev_G.edges()])
        if not ev_pairs_df.empty:
            st.plotly_chart(
                _dept_heatmap(ev_pairs_df, "발신자_부서", "수신자_부서", "부서 간 행사 동반 참여 강도"),
                use_container_width=True,
            )

        st.divider()
        st.subheader("🤝 자주 함께하는 직원 쌍 TOP")
        edge_rows = sorted(
            [{"직원A": u, "직원B": v,
              "A부서": ev_G.nodes[u].get("department",""),
              "B부서": ev_G.nodes[v].get("department",""),
              "공동참여 횟수": d["co_events"]}
             for u, v, d in ev_G.edges(data=True)],
            key=lambda x: x["공동참여 횟수"], reverse=True
        )[:20]
        df_edge = pd.DataFrame(edge_rows)
        df_edge["같은부서"] = df_edge["A부서"] == df_edge["B부서"]
        st.dataframe(df_edge, use_container_width=True, height=280)

        fig_ep = px.bar(
            df_edge.head(15),
            x="공동참여 횟수",
            y=[f"{r['직원A']}↔{r['직원B']}" for r in edge_rows[:15]],
            orientation="h", color="같은부서",
            title="자주 함께한 직원 쌍",
            color_discrete_map={True:"#4472C4", False:"#ED7D31"},
        )
        st.plotly_chart(fig_ep, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════
# TAB 5 – Combined Network
# ══════════════════════════════════════════════════════════════════════
with tab_combined:
    st.header("🔗 통합 네트워크 분석")
    result = st.session_state.get("result")

    if result is None:
        st.info("분석을 실행하세요.")
    else:
        top_n_val = st.session_state.get("top_n", 15)
        G_c = result.graph

        ea, eb, ec = st.columns(3)
        ea.metric("전체 직원 노드", G_c.number_of_nodes())
        eb.metric("전체 연결 엣지", G_c.number_of_edges())
        edge_src = result.edge_source_stats
        ec.metric("행사+리뷰 양쪽 연결", edge_src.get("both", 0))

        c1, c2 = st.columns([3, 2])
        with c1:
            color_opt = st.radio("노드 색상", ["부서","커뮤니티"], horizontal=True, key="comb_color")
            html_c = build_pyvis_html(G_c, result, color_by="department" if color_opt == "부서" else "community")
            components.html(html_c, height=600, scrolling=False)
            st.caption("🔵 행사 연결 | 🟠 피어리뷰 연결 | 🟢 양쪽 모두")

        with c2:
            st.subheader("🌟 영향력 지수 TOP")
            dc = result.degree_centrality
            bc = result.betweenness_centrality
            inf = {n: dc.get(n,0)*0.5 + bc.get(n,0)*0.5 for n in G_c.nodes()}
            top_inf = sorted(inf.items(), key=lambda x: x[1], reverse=True)[:top_n_val]
            df_inf = pd.DataFrame(top_inf, columns=["직원","영향력"])
            df_inf["부서"] = df_inf["직원"].map(lambda n: G_c.nodes[n].get("department",""))
            fig_inf = px.bar(df_inf.head(12), x="영향력", y="직원", orientation="h",
                             color="부서", title="영향력 지수 TOP")
            fig_inf.update_layout(yaxis={"categoryorder":"total ascending"})
            st.plotly_chart(fig_inf, use_container_width=True)

        st.divider()
        st.subheader("📊 중심성 분석")
        metric_sel = st.radio("지표", ["연결 중심성 (Degree)","매개 중심성 (Betweenness)","근접 중심성 (Closeness)"],
                              horizontal=True, key="comb_metric")
        m_key = {"연결 중심성 (Degree)":"degree","매개 중심성 (Betweenness)":"betweenness","근접 중심성 (Closeness)":"closeness"}[metric_sel]
        bar_df = build_centrality_bar_df(result, metric=m_key, top_n=top_n_val)
        fig_bar = px.bar(bar_df, x="점수", y="이름", orientation="h", color="부서",
                         title=f"Top {top_n_val} — {metric_sel}", height=max(400, top_n_val*28))
        fig_bar.update_layout(yaxis={"categoryorder":"total ascending"})
        st.plotly_chart(fig_bar, use_container_width=True)

        st.divider()
        st.subheader("🗺 부서 간 연결 히트맵")
        if not result.dept_heatmap_df.empty:
            fig_h = px.imshow(result.dept_heatmap_df, text_auto=True,
                              color_continuous_scale="Blues",
                              title="부서 × 부서 연결 강도", aspect="auto")
            fig_h.update_layout(height=450)
            st.plotly_chart(fig_h, use_container_width=True)

        # Edge source overlap bar
        st.divider()
        st.subheader("데이터 소스별 엣지 분포")
        fig_src = px.pie(
            values=[edge_src.get("event",0), edge_src.get("review",0), edge_src.get("both",0)],
            names=["행사만","피어리뷰만","양쪽 모두"],
            color_discrete_sequence=["#4a9eff","#ff8c42","#2ecc71"],
            title="엣지 소스 구성")
        st.plotly_chart(fig_src, use_container_width=True)

        # Radar for top-5 influential employees
        st.divider()
        st.subheader("🎯 핵심 직원 중심성 프로파일 (Radar)")
        top5 = [n for n, _ in top_inf[:5]]
        if len(top5) >= 2:
            cats = ["연결 중심성","매개 중심성","근접 중심성"]
            vals_map = {
                "연결 중심성": result.degree_centrality,
                "매개 중심성": result.betweenness_centrality,
                "근접 중심성": result.closeness_centrality,
            }
            max_vals = {c: max(v for v in vals_map[c].values()) or 1 for c in cats}
            fig_rad = go.Figure()
            colors = px.colors.qualitative.Set1
            for i, node in enumerate(top5):
                norm = [vals_map[c].get(node, 0) / max_vals[c] for c in cats]
                norm.append(norm[0])
                fig_rad.add_trace(go.Scatterpolar(
                    r=norm, theta=cats + [cats[0]],
                    name=str(node), line_color=colors[i % len(colors)]
                ))
            fig_rad.update_layout(
                polar=dict(radialaxis=dict(visible=True, range=[0,1])),
                title="핵심 직원 중심성 프로파일 (정규화)", height=420,
            )
            st.plotly_chart(fig_rad, use_container_width=True)

        # Download
        st.divider()
        csv = result.summary_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 중심성 요약 CSV 다운로드", csv, "network_summary.csv", "text/csv")

# ══════════════════════════════════════════════════════════════════════
# TAB 6 – Hypothesis Testing
# ══════════════════════════════════════════════════════════════════════
with tab_hyp:
    st.header("🔬 가설 검증 분석")

    peer_records = st.session_state.get("peer_records", [])
    msg_records  = st.session_state.get("msg_records", [])
    event_groups = st.session_state.get("event_groups", {})
    cross        = st.session_state.get("cross")

    # ── 가설 1 ──────────────────────────────────────────────────────────
    st.markdown('<div class="hyp-box">💡 <b>가설 1</b>: 본인을 리뷰어로 추천한 경우, 리뷰어가 해당 인물에게 호의적이기 때문에 선정했을 것이다.</div>', unsafe_allow_html=True)

    if peer_records:
        pr_set = {(r.requester_id, r.reviewer_id) for r in peer_records}
        mutual_pairs = [(u, v) for (u, v) in pr_set if (v, u) in pr_set and u < v]

        total_pr = len(pr_set)
        mut_rate = len(mutual_pairs) / total_pr * 100 if total_pr else 0

        h1a, h1b = st.columns(2)
        with h1a:
            st.metric("전체 추천 건수", total_pr)
            st.metric("상호 추천 쌍 수", len(mutual_pairs))
            st.metric("상호 추천 비율", f"{mut_rate:.1f}%")

            # Compare freq distribution: mutual vs one-way
            pr_freq = {(r.requester_id, r.reviewer_id): r.collab_frequency for r in peer_records}
            mut_freqs = [pr_freq.get((u,v),"") for u,v in mutual_pairs] + [pr_freq.get((v,u),"") for u,v in mutual_pairs]
            oneway_freqs = [r.collab_frequency for r in peer_records if (r.reviewer_id, r.requester_id) not in pr_set]

            mut_cnt   = Counter(f for f in mut_freqs   if f)
            oneway_cnt= Counter(f for f in oneway_freqs if f)
            all_cats  = COLLAB_FREQUENCIES

            compare_df = pd.DataFrame({
                "협업빈도": all_cats * 2,
                "유형": ["상호 추천"]*len(all_cats) + ["단방향 추천"]*len(all_cats),
                "수": [mut_cnt.get(c,0) for c in all_cats] + [oneway_cnt.get(c,0) for c in all_cats],
            })
            fig_mut = px.bar(compare_df, x="협업빈도", y="수", color="유형", barmode="group",
                             title="상호 vs 단방향 추천: 협업 빈도 비교",
                             category_orders={"협업빈도": COLLAB_FREQUENCIES},
                             color_discrete_sequence=["#4472C4","#ED7D31"])
            fig_mut.update_xaxes(tickangle=15)
            st.plotly_chart(fig_mut, use_container_width=True)

        with h1b:
            # Same dept in mutual vs one-way
            mut_same  = sum(1 for u,v in mutual_pairs
                            if next((r.requester_dept for r in peer_records if r.requester_id==u and r.reviewer_id==v), "") ==
                               next((r.reviewer_dept  for r in peer_records if r.requester_id==u and r.reviewer_id==v), ""))
            ow_same   = sum(1 for r in peer_records if (r.reviewer_id, r.requester_id) not in pr_set
                            and r.requester_dept == r.reviewer_dept)
            ow_total  = sum(1 for r in peer_records if (r.reviewer_id, r.requester_id) not in pr_set)

            fig_dept_compare = px.bar(
                x=["상호 추천","단방향 추천"],
                y=[mut_same/max(len(mutual_pairs),1)*100, ow_same/max(ow_total,1)*100],
                title="같은 부서 내 추천 비율 (%): 상호 vs 단방향",
                labels={"x":"구분","y":"같은 부서 비율(%)"},
                color=["상호 추천","단방향 추천"],
                color_discrete_sequence=["#4472C4","#ED7D31"],
            )
            st.plotly_chart(fig_dept_compare, use_container_width=True)

            if mut_rate > 20:
                st.markdown('<div class="insight-box">상호 추천 비율이 높아 <b>리뷰어 선정에 상호 친밀도</b>가 반영되었을 가능성이 있습니다.</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="insight-box">상호 추천 비율이 낮아 호의적 편향보다는 <b>업무 기반 선정</b>이 우세할 수 있습니다.</div>', unsafe_allow_html=True)
    else:
        st.info("피어리뷰 데이터를 분석한 뒤 확인하세요.")

    st.divider()

    # ── 가설 2 ──────────────────────────────────────────────────────────
    st.markdown('<div class="hyp-box">💡 <b>가설 2</b>: 협업 빈도가 낮음에도 리뷰어로 선정한 경우, 친목(사적 관계)이 영향을 미쳤을 것이다.</div>', unsafe_allow_html=True)

    if peer_records and msg_records:
        low_freq_recs = [r for r in peer_records if r.collab_frequency == "주 1회 미만"]
        st.metric("저빈도(주 1회 미만) 추천 건수", len(low_freq_recs),
                  f"{len(low_freq_recs)/max(len(peer_records),1)*100:.1f}%")

        # Check if low-freq pairs also message each other
        msg_pair_set: set[frozenset] = set()
        for r in msg_records:
            msg_pair_set.add(frozenset([r.sender_id, r.receiver_id]))

        h2a, h2b = st.columns(2)
        with h2a:
            lf_with_msg    = sum(1 for r in low_freq_recs if frozenset([r.requester_id, r.reviewer_id]) in msg_pair_set)
            lf_without_msg = len(low_freq_recs) - lf_with_msg
            msg_cross_rate = lf_with_msg / max(len(low_freq_recs), 1) * 100

            fig_lf = px.pie(values=[lf_with_msg, lf_without_msg],
                            names=["메시지 교류 있음","메시지 교류 없음"],
                            title="저빈도 추천 쌍의 메시지 교류 여부",
                            color_discrete_sequence=["#2ECC71","#E74C3C"])
            st.plotly_chart(fig_lf, use_container_width=True)
            st.metric("저빈도 추천 중 메시지 교류 비율", f"{msg_cross_rate:.1f}%",
                      help="높을수록 친목 관계가 리뷰어 선정에 영향을 미쳤을 가능성 ↑")

        with h2b:
            # Compare: low-freq same-dept vs cross-dept
            lf_same  = [r for r in low_freq_recs if r.requester_dept == r.reviewer_dept]
            lf_cross = [r for r in low_freq_recs if r.requester_dept != r.reviewer_dept]
            fig_lf2 = px.pie(values=[len(lf_same), len(lf_cross)],
                             names=["같은 부서","다른 부서"],
                             title="저빈도 추천: 부서 구성",
                             color_discrete_sequence=["#4472C4","#ED7D31"])
            st.plotly_chart(fig_lf2, use_container_width=True)

            if msg_cross_rate > 35:
                st.markdown('<div class="insight-box">저빈도 협업임에도 메시지 교류가 많아 <b>사적 친밀도가 리뷰어 선정에 영향</b>을 미쳤을 가능성이 높습니다.</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="insight-box">저빈도 추천 중 메시지 교류 비율이 낮아, 친목보다 <b>다른 요인(업무 가시성 등)</b>이 작용했을 수 있습니다.</div>', unsafe_allow_html=True)
    elif peer_records:
        st.info("메시지 데이터도 함께 분석하면 교차 검증이 가능합니다.")
    else:
        st.info("피어리뷰 데이터를 분석한 뒤 확인하세요.")

    st.divider()

    # ── 가설 3 ──────────────────────────────────────────────────────────
    st.markdown('<div class="hyp-box">💡 <b>가설 3</b>: 사내 행사에 같이 참여하는 인력들은 평소 친한 무리일 것이다.</div>', unsafe_allow_html=True)

    if event_groups and (msg_records or peer_records):
        ev_pair_set: set[frozenset] = {
            frozenset([a.employee_id, b.employee_id])
            for grps in event_groups.values()
            for grp in grps if len(grp) >= 2
            for a, b in combinations(grp, 2)
        }

        h3a, h3b = st.columns(2)

        with h3a:
            if msg_records:
                # event co-participants who also message each other
                ev_msg_overlap = sum(1 for p in ev_pair_set if p in msg_pair_set)
                st.metric("행사 동반 참여 쌍", len(ev_pair_set))
                st.metric("그 중 메시지 교류 있는 쌍", ev_msg_overlap)
                st.metric("메시지 교류 비율", f"{ev_msg_overlap/max(len(ev_pair_set),1)*100:.1f}%")

                fig_h3 = go.Figure(go.Bar(
                    x=["행사 동반 참여 쌍"],
                    y=[ev_msg_overlap],
                    name="메시지 교류 있음",
                    marker_color="#2ECC71",
                ))
                fig_h3.add_trace(go.Bar(
                    x=["행사 동반 참여 쌍"],
                    y=[len(ev_pair_set) - ev_msg_overlap],
                    name="메시지 교류 없음",
                    marker_color="#E74C3C",
                ))
                fig_h3.update_layout(barmode="stack", title="행사 동반 참여 쌍의 메시지 교류")
                st.plotly_chart(fig_h3, use_container_width=True)

        with h3b:
            if cross:
                st.metric("행사 기반 쌍", cross["행사 기반 쌍 수"])
                st.metric("피어리뷰 기반 쌍", cross["피어리뷰 기반 쌍 수"])
                st.metric("양쪽 일치 쌍", cross["양쪽 모두 해당 쌍 수"])
                st.metric("교차 비율", f"{cross['교차 비율 (%)']}%",
                          help="행사 동반 참여 쌍 중 피어리뷰에서도 연결된 비율")

                cr = cross["교차 비율 (%)"]
                if cr > 20:
                    st.markdown('<div class="insight-box">행사 동반 참여자 중 상당수가 피어리뷰에서도 연결되어, <b>행사 참여와 업무 친밀도의 연관성</b>을 시사합니다.</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div class="insight-box">교차 비율이 낮아 행사 참여가 <b>반드시 피어리뷰 선정으로 이어지지는 않는</b> 것으로 보입니다.</div>', unsafe_allow_html=True)
    elif event_groups:
        st.info("메시지 또는 피어리뷰 데이터도 함께 로드하면 교차 검증이 가능합니다.")
    else:
        st.info("행사 데이터를 분석한 뒤 확인하세요.")

    # ── 종합 인사이트 ────────────────────────────────────────────────────
    st.divider()
    st.subheader("💡 종합 인사이트")

    insights = []
    if peer_records:
        total_pr = len({(r.requester_id, r.reviewer_id) for r in peer_records})
        mut_cnt_val = len([(u,v) for (u,v) in {(r.requester_id, r.reviewer_id) for r in peer_records}
                           if ({(r.requester_id, r.reviewer_id) for r in peer_records}) and
                           (v,u) in {(r.requester_id, r.reviewer_id) for r in peer_records} and u < v])
        same_dept_rate = sum(1 for r in peer_records if r.requester_dept == r.reviewer_dept) / max(len(peer_records),1) * 100
        insights.append(f"• 피어리뷰 추천 중 **같은 부서 내 추천 비율: {same_dept_rate:.0f}%** — 업무 가시성이 리뷰어 선정에 주요 요인임.")

    if peer_records and msg_records:
        low_freq_recs2 = [r for r in peer_records if r.collab_frequency == "주 1회 미만"]
        if low_freq_recs2:
            lfw = sum(1 for r in low_freq_recs2 if frozenset([r.requester_id, r.reviewer_id]) in msg_pair_set)
            rate = lfw / len(low_freq_recs2) * 100
            insights.append(f"• 저빈도 협업 추천의 **{rate:.0f}%가 메시지 교류** 동반 — 친목 관계가 리뷰어 선정에 부분 영향.")

    if cross:
        insights.append(f"• 행사 동반 참여 쌍 중 **{cross['교차 비율 (%)']}%가 피어리뷰에서도 연결** — 두 가설 간 교차 일관성 {'높음' if cross['교차 비율 (%)'] > 20 else '낮음'}.")

    if not insights:
        insights.append("• 분석 실행 후 데이터가 충분하면 인사이트가 자동 표시됩니다.")

    for ins in insights:
        st.markdown(f'<div class="insight-box">{ins}</div>', unsafe_allow_html=True)
