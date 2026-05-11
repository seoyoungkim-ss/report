"""
Confluence 기반 임직원 인별 종합 분석 시스템
- Confluence 페이지 데이터 + 피어리뷰 + 메시지 + 행사 참여 통합 프로파일
- hr_network_app.py 분석 결과와 연동 가능 (세션 공유)
"""
from __future__ import annotations

import warnings
from collections import Counter, defaultdict
from itertools import combinations

import networkx as nx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from src.confluence_extractor import (
    ConfluenceClient,
    ConfluencePage,
    ConfluenceComment,
    build_coauthor_network_df,
    build_mention_network_df,
    build_page_df,
    build_user_activity_df,
    compute_knowledge_score,
    generate_sample_confluence_data,
    SPACE_NAMES,
    ALL_LABELS,
)
from src.network_analyzer import (
    generate_sample_attendance,
    generate_sample_peerreview,
    generate_sample_messages,
    parse_attendance_dataframe,
    parse_peerreview_dataframe,
    parse_message_dataframe,
    group_by_time_window,
    build_network,
    run_analysis,
    build_pyvis_html,
    GroupingConfig,
    detect_column_mapping,
)

warnings.filterwarnings("ignore")

st.set_page_config(
    page_title="임직원 종합 분석 (Confluence)",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.metric-card {background:#f0f4ff;padding:12px 16px;border-left:4px solid #4472c4;
              border-radius:4px;margin-bottom:8px;}
.score-badge {display:inline-block;background:#4472c4;color:#fff;padding:3px 10px;
              border-radius:12px;font-size:.85rem;font-weight:700;}
.section-title {font-size:1.1rem;font-weight:700;color:#1f3864;margin:10px 0 4px;}
.insight-box {background:#e8f5e9;padding:8px 12px;border-left:4px solid #2e7d32;
              border-radius:4px;font-size:.9rem;margin:4px 0;}
.warn-box    {background:#fff8e7;padding:8px 12px;border-left:4px solid #f0a500;
              border-radius:4px;font-size:.9rem;margin:4px 0;}
</style>
""", unsafe_allow_html=True)


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _read_file(uploaded) -> pd.DataFrame:
    return (pd.read_csv(uploaded) if uploaded.name.lower().endswith(".csv")
            else pd.read_excel(uploaded))


def _pyvis_html_from_nx(G: nx.Graph | nx.DiGraph,
                         node_color_attr: str = "dept",
                         directed: bool = False,
                         height: int = 560) -> str:
    from pyvis.network import Network
    palette = ["#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6",
               "#1abc9c","#e67e22","#34495e","#e91e63","#00bcd4",
               "#8bc34a","#ff5722","#607d8b","#795548","#ffeb3b"]
    attr_vals = sorted({G.nodes[n].get(node_color_attr,"기타") for n in G.nodes()})
    color_map = {v: palette[i % len(palette)] for i, v in enumerate(attr_vals)}

    net = Network(height=f"{height}px", width="100%", bgcolor="#1a1a2e",
                  font_color="#fff", directed=directed, notebook=False)
    net.set_options("""{"physics":{"forceAtlas2Based":{"gravitationalConstant":-50,
      "springLength":110},"solver":"forceAtlas2Based",
      "stabilization":{"iterations":120}},
      "edges":{"smooth":{"type":"continuous"}},
      "interaction":{"hover":true,"navigationButtons":true}}""")

    deg = dict(G.degree())
    max_d = max(deg.values(), default=1)
    for n in G.nodes():
        val = G.nodes[n].get(node_color_attr, "기타")
        size = 10 + (deg[n] / max_d) * 35
        attrs = dict(G.nodes[n])
        tip = "<br>".join(f"{k}: {v}" for k, v in attrs.items())
        net.add_node(str(n), label=str(n), title=f"{n}<br>{tip}",
                     color=color_map.get(val,"#888"), size=size)

    max_w = max((d.get("weight",1) for _,_,d in G.edges(data=True)), default=1)
    for u, v, d in G.edges(data=True):
        w = d.get("weight", d.get("공동기여 횟수", 1))
        net.add_edge(str(u), str(v), width=1+(w/max_w)*6,
                     title=f"연결강도: {w}", color="#aaaaaa55")
    return net.generate_html()


def _radar_chart(categories: list[str], values_map: dict[str, list[float]]) -> go.Figure:
    fig = go.Figure()
    colors = px.colors.qualitative.Set1
    for i, (name, vals) in enumerate(values_map.items()):
        closed = vals + [vals[0]]
        fig.add_trace(go.Scatterpolar(
            r=closed, theta=categories + [categories[0]],
            name=name, fill="toself",
            line_color=colors[i % len(colors)],
            opacity=0.75,
        ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True, height=380,
    )
    return fig


# ────────────────────────────────────────────────────────────────────
# Sidebar — Confluence connection & data loading
# ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("📚 Confluence 분석")
    st.caption("임직원 인별 종합 프로파일")
    st.divider()

    data_mode = st.radio("데이터 소스", ["샘플 데이터", "Confluence API 연결", "Excel/CSV 업로드"],
                         index=0)

    if data_mode == "샘플 데이터":
        n_emp = st.slider("샘플 직원 수", 20, 60, 30, 5)
        if st.button("📦 샘플 데이터 생성", use_container_width=True, type="primary"):
            with st.spinner("데이터 생성 중…"):
                att_df  = generate_sample_attendance()
                # Keep only n_emp employees
                emp_ids_keep = att_df["사번"].unique()[:n_emp]
                att_df  = att_df[att_df["사번"].isin(emp_ids_keep)]
                peer_df = generate_sample_peerreview(att_df)
                msg_df  = generate_sample_messages(att_df)
                pages, comments, cf_map = generate_sample_confluence_data(att_df)

                st.session_state.update({
                    "cf_att_df":   att_df,
                    "cf_peer_df":  peer_df,
                    "cf_msg_df":   msg_df,
                    "cf_pages":    pages,
                    "cf_comments": comments,
                    "cf_id_map":   cf_map,           # confluence_id → 사번
                    "cf_data_ready": True,
                })
            st.success(f"완료 — 직원 {n_emp}명, 페이지 {len(pages)}개")

    elif data_mode == "Confluence API 연결":
        st.subheader("API 설정")
        cf_url    = st.text_input("Confluence URL", placeholder="https://your-domain.atlassian.net")
        cf_email  = st.text_input("이메일 / 사용자명")
        cf_token  = st.text_input("API Token / 비밀번호", type="password")
        is_cloud  = st.checkbox("Atlassian Cloud", value=True)

        st.subheader("크롤링할 스페이스")
        space_input = st.text_input("스페이스 키 (쉼표 구분)", placeholder="HR,DEV,MKT")
        cql_filter  = st.text_input("추가 CQL 필터 (선택)", placeholder="lastModified >= '2025-01-01'")

        if st.button("🔗 연결 테스트"):
            if cf_url and cf_email and cf_token:
                client = ConfluenceClient(cf_url, cf_email, cf_token, is_cloud)
                ok, msg_txt = client.test_connection()
                if ok:
                    st.success(msg_txt)
                    st.session_state["cf_client"] = client
                else:
                    st.error(msg_txt)
            else:
                st.warning("URL, 이메일, 토큰을 모두 입력하세요.")

        if st.button("📥 데이터 수집 시작", type="primary"):
            client = st.session_state.get("cf_client")
            if client is None:
                st.error("먼저 연결 테스트를 통과하세요.")
            else:
                space_keys = [s.strip() for s in space_input.split(",") if s.strip()]
                prog = st.progress(0, "수집 중…")
                def _cb(pct, msg_t):
                    prog.progress(pct, msg_t)
                pages, comments = client.fetch_all_pages_from_spaces(space_keys, cql_filter, _cb)
                prog.empty()
                st.session_state.update({
                    "cf_pages": pages, "cf_comments": comments,
                    "cf_id_map": {},   # user maps separately
                    "cf_data_ready": True,
                })
                st.success(f"수집 완료 — 페이지 {len(pages)}개, 댓글 {len(comments)}개")

    else:  # Upload
        st.subheader("파일 업로드")
        st.caption("Confluence 내보내기 CSV (page_id, title, space, author, created, labels 등)")
        pages_file   = st.file_uploader("페이지 데이터",   type=["csv","xlsx"])
        comment_file = st.file_uploader("댓글 데이터 (선택)", type=["csv","xlsx"])
        id_map_file  = st.file_uploader("ID 매핑 (선택, confluence_id→사번)", type=["csv","xlsx"])

        if pages_file:
            raw_page_df = _read_file(pages_file)
            st.session_state["cf_raw_page_df"] = raw_page_df
            st.session_state["cf_data_ready_upload"] = True

    # ── ID Mapping section (for API / Upload mode) ───────────────────
    if data_mode != "샘플 데이터" and st.session_state.get("cf_data_ready"):
        st.divider(); st.subheader("🗂 ID 매핑 설정")
        st.caption("Confluence 사용자 ID ↔ 사번 연결")
        id_map_text = st.text_area(
            "confluence_id,사번 (한 줄씩)",
            placeholder="user_abc123,EMP001\nuser_def456,EMP002",
            height=120,
        )
        if id_map_text.strip():
            cf_map: dict[str, str] = {}
            for line in id_map_text.strip().splitlines():
                parts = line.split(",")
                if len(parts) == 2:
                    cf_map[parts[0].strip()] = parts[1].strip()
            st.session_state["cf_id_map"] = cf_map

    # ── Other HR data (optional) ─────────────────────────────────────
    st.divider(); st.subheader("📎 HR 데이터 연동 (선택)")
    st.caption("hr_network_app.py 에서 분석한 데이터가 자동 참조됩니다.")
    has_hr = all(k in st.session_state for k in ["cf_peer_df","cf_msg_df","cf_att_df"])
    if has_hr:
        st.success("HR 데이터 연동됨 ✓")
    else:
        st.info("HR 데이터 없음 — Confluence 분석만 수행")


# ────────────────────────────────────────────────────────────────────
# Load & prepare data
# ────────────────────────────────────────────────────────────────────

if not st.session_state.get("cf_data_ready"):
    st.title("📚 Confluence 기반 임직원 종합 분석")
    st.info("👈 사이드바에서 데이터를 설정하세요.")
    st.markdown("""
| 탭 | 내용 |
|---|---|
| 📊 데이터 현황 | Confluence 페이지/스페이스 전체 현황 |
| 👤 인별 Confluence 분석 | 직원별 문서 기여도, 주제 전문성, 멘션 관계 |
| 🕸 지식 네트워크 | 공동편집·멘션 기반 협업 네트워크 시각화 |
| 🏆 종합 인재 프로파일 | Confluence + 피어리뷰 + 메시지 + 행사 4소스 통합 |
| 📈 부서별 비교 | 부서 단위 지식문화·협업 수준 비교 |
""")
    st.stop()

pages: list[ConfluencePage]   = st.session_state.get("cf_pages", [])
comments: list[ConfluenceComment] = st.session_state.get("cf_comments", [])
cf_id_map: dict[str, str]        = st.session_state.get("cf_id_map", {})

# Reverse map: 사번 → confluence_id
emp_to_cf = {v: k for k, v in cf_id_map.items()}

# Build DataFrames (cache in session)
if "cf_page_df" not in st.session_state or not st.session_state["cf_page_df"] is not None:
    page_df      = build_page_df(pages)
    activity_df  = build_user_activity_df(pages, comments, cf_id_map)
    activity_df  = compute_knowledge_score(activity_df)
    mention_df   = build_mention_network_df(pages, cf_id_map)
    coauthor_df  = build_coauthor_network_df(pages, cf_id_map)

    st.session_state.update({
        "cf_page_df":     page_df,
        "cf_activity_df": activity_df,
        "cf_mention_df":  mention_df,
        "cf_coauthor_df": coauthor_df,
    })

page_df:     pd.DataFrame = st.session_state["cf_page_df"]
activity_df: pd.DataFrame = st.session_state["cf_activity_df"]
mention_df:  pd.DataFrame = st.session_state["cf_mention_df"]
coauthor_df: pd.DataFrame = st.session_state["cf_coauthor_df"]

# HR data
att_df  = st.session_state.get("cf_att_df")
peer_df = st.session_state.get("cf_peer_df")
msg_df  = st.session_state.get("cf_msg_df")

# Dept mapping (사번 → 부서)
emp_dept: dict[str, str] = {}
if att_df is not None and "사번" in att_df.columns and "부서" in att_df.columns:
    emp_dept = att_df.drop_duplicates("사번").set_index("사번")["부서"].to_dict()
# Supplement from cf_id_map
activity_df["부서"] = activity_df["사번"].map(emp_dept).fillna("미분류")

# ────────────────────────────────────────────────────────────────────
# Tabs
# ────────────────────────────────────────────────────────────────────

tab_overview, tab_person, tab_netw, tab_profile, tab_dept = st.tabs([
    "📊 데이터 현황",
    "👤 인별 Confluence 분석",
    "🕸 지식 네트워크",
    "🏆 종합 인재 프로파일",
    "📈 부서별 비교",
])

# ══════════════════════════════════════════════════════════════════════
# TAB 1 — Overview
# ══════════════════════════════════════════════════════════════════════
with tab_overview:
    st.header("📊 Confluence 데이터 현황")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("총 페이지 수",  len(page_df))
    m2.metric("스페이스 수",   page_df["스페이스"].nunique() if not page_df.empty else 0)
    m3.metric("작성자 수",     activity_df["사번"].nunique() if not activity_df.empty else 0)
    m4.metric("총 댓글 수",    len(comments))
    m5.metric("총 누적 조회수", int(page_df["조회수"].sum()) if not page_df.empty else 0)

    st.divider()
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("📁 스페이스별 페이지 분포")
        if not page_df.empty:
            sp_cnt = page_df["스페이스"].value_counts().reset_index()
            sp_cnt.columns = ["스페이스","페이지 수"]
            fig = px.bar(sp_cnt, x="스페이스", y="페이지 수", color="스페이스",
                         color_discrete_sequence=px.colors.qualitative.Set2)
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("🏷 레이블 사용 빈도")
        if not page_df.empty:
            all_labels_flat = [l for row in page_df["레이블"] for l in row.split(",") if l.strip()]
            label_cnt = Counter(all_labels_flat)
            df_lbl = pd.DataFrame(label_cnt.most_common(15), columns=["레이블","빈도"])
            fig_lbl = px.bar(df_lbl, x="레이블", y="빈도",
                             color="빈도", color_continuous_scale="Blues")
            st.plotly_chart(fig_lbl, use_container_width=True)

    with c2:
        st.subheader("📅 월별 페이지 생성 트렌드")
        if not page_df.empty and "생성일" in page_df.columns:
            page_df["월"] = pd.to_datetime(page_df["생성일"]).dt.to_period("M").astype(str)
            monthly = page_df.groupby(["월","스페이스"]).size().reset_index(name="페이지 수")
            fig_tr = px.line(monthly, x="월", y="페이지 수", color="스페이스",
                             markers=True, title="월별·스페이스별 페이지 생성")
            st.plotly_chart(fig_tr, use_container_width=True)

        st.subheader("👁 조회수 TOP 페이지")
        if not page_df.empty:
            top_views = page_df.nlargest(10, "조회수")[["제목","스페이스","조회수","댓글수"]]
            st.dataframe(top_views, use_container_width=True, height=260)

    st.divider()
    st.subheader("👥 직원별 Confluence 활동 요약")
    show_cols = ["사번","부서","작성 페이지","기여 페이지","댓글 작성",
                 "멘션 수신","누적 조회수","활동 스페이스 수","confluence_score"]
    st.dataframe(
        activity_df[[c for c in show_cols if c in activity_df.columns]].head(30),
        use_container_width=True, height=340,
    )
    if not activity_df.empty:
        csv = activity_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 활동 데이터 CSV 다운로드", csv, "confluence_activity.csv", "text/csv")


# ══════════════════════════════════════════════════════════════════════
# TAB 2 — Per-Employee Confluence Analysis
# ══════════════════════════════════════════════════════════════════════
with tab_person:
    st.header("👤 인별 Confluence 분석")

    all_emp_ids = sorted(activity_df["사번"].dropna().unique().tolist())
    if not all_emp_ids:
        st.info("데이터가 없습니다."); st.stop()

    sel_emp = st.selectbox("분석할 직원 선택", all_emp_ids)
    sel_cf  = emp_to_cf.get(sel_emp, sel_emp)

    row = activity_df[activity_df["사번"] == sel_emp]
    if row.empty:
        st.warning("해당 직원의 Confluence 활동 데이터가 없습니다.")
    else:
        r = row.iloc[0]
        dept = emp_dept.get(sel_emp, r.get("부서","미분류"))

        st.markdown(f"**{sel_emp}** | 부서: **{dept}** | "
                    f'Confluence 점수: <span class="score-badge">{r["confluence_score"]:.0f}</span>',
                    unsafe_allow_html=True)
        st.divider()

        # Metrics row
        mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
        mc1.metric("작성 페이지",   int(r["작성 페이지"]))
        mc2.metric("기여 페이지",   int(r["기여 페이지"]))
        mc3.metric("댓글 작성",     int(r["댓글 작성"]))
        mc4.metric("멘션 수신",     int(r["멘션 수신"]))
        mc5.metric("누적 조회수",   int(r["누적 조회수"]))
        mc6.metric("활동 스페이스", int(r["활동 스페이스 수"]))

        st.divider()
        pa, pb = st.columns(2)

        with pa:
            # Pages authored by this employee
            authored = [p for p in pages if cf_id_map.get(p.author_id, p.author_id) == sel_emp]
            st.markdown('<p class="section-title">📄 작성 페이지</p>', unsafe_allow_html=True)
            if authored:
                auth_df = pd.DataFrame([{
                    "제목": p.title, "스페이스": p.space_name,
                    "생성일": p.created_at.strftime("%Y-%m-%d"),
                    "조회수": p.view_count, "댓글": p.comment_count,
                    "단어수": p.word_count,
                } for p in sorted(authored, key=lambda x: x.created_at, reverse=True)])
                st.dataframe(auth_df, use_container_width=True, height=240)

                # Space distribution
                sp_dist = Counter(p.space_name for p in authored)
                fig_sp = px.pie(values=list(sp_dist.values()), names=list(sp_dist.keys()),
                                title="작성 페이지 스페이스 분포",
                                color_discrete_sequence=px.colors.qualitative.Pastel)
                st.plotly_chart(fig_sp, use_container_width=True)
            else:
                st.info("작성 페이지 없음")

        with pb:
            # Pages contributed to
            contributed = [p for p in pages
                           if sel_cf in p.contributors
                           and cf_id_map.get(p.author_id, p.author_id) != sel_emp]
            st.markdown('<p class="section-title">✏️ 기여 페이지</p>', unsafe_allow_html=True)
            if contributed:
                cont_df = pd.DataFrame([{
                    "제목": p.title,
                    "스페이스": p.space_name,
                    "원작성자": cf_id_map.get(p.author_id, p.author_id),
                    "생성일": p.created_at.strftime("%Y-%m-%d"),
                } for p in contributed])
                st.dataframe(cont_df, use_container_width=True, height=240)

                # Topic coverage via labels
                all_pg_labels = [l for p in authored + contributed for l in p.labels]
                if all_pg_labels:
                    lbl_cnt = Counter(all_pg_labels)
                    fig_lbl2 = px.bar(
                        pd.DataFrame(lbl_cnt.most_common(10), columns=["레이블","빈도"]),
                        x="레이블", y="빈도", title="주요 레이블",
                        color="빈도", color_continuous_scale="Teal",
                    )
                    st.plotly_chart(fig_lbl2, use_container_width=True)
            else:
                st.info("기여 페이지 없음")

        st.divider()

        # Mention network for this employee
        st.markdown('<p class="section-title">🔗 멘션 관계</p>', unsafe_allow_html=True)
        if not mention_df.empty:
            sent_by   = mention_df[mention_df["from_emp"] == sel_emp]["to_emp"].value_counts().head(10)
            recv_from = mention_df[mention_df["to_emp"]   == sel_emp]["from_emp"].value_counts().head(10)

            ma, mb = st.columns(2)
            with ma:
                st.caption("내가 언급한 사람")
                if not sent_by.empty:
                    fig_sb = px.bar(x=sent_by.values, y=sent_by.index, orientation="h",
                                    color=sent_by.values, color_continuous_scale="Blues",
                                    labels={"x":"언급 수","y":"사번"})
                    fig_sb.update_layout(yaxis={"categoryorder":"total ascending"}, height=280)
                    st.plotly_chart(fig_sb, use_container_width=True)
                else:
                    st.info("멘션한 직원 없음")
            with mb:
                st.caption("나를 언급한 사람")
                if not recv_from.empty:
                    fig_rb = px.bar(x=recv_from.values, y=recv_from.index, orientation="h",
                                    color=recv_from.values, color_continuous_scale="Reds",
                                    labels={"x":"언급 수","y":"사번"})
                    fig_rb.update_layout(yaxis={"categoryorder":"total ascending"}, height=280)
                    st.plotly_chart(fig_rb, use_container_width=True)
                else:
                    st.info("나를 언급한 직원 없음")

        # Monthly activity timeline
        st.divider()
        st.markdown('<p class="section-title">📅 월별 활동 타임라인</p>', unsafe_allow_html=True)
        if authored or contributed:
            timeline_rows = (
                [{"월": p.created_at.strftime("%Y-%m"), "유형": "작성"} for p in authored] +
                [{"월": p.created_at.strftime("%Y-%m"), "유형": "기여"} for p in contributed] +
                [{"월": c.created_at.strftime("%Y-%m"), "유형": "댓글"}
                 for c in comments if cf_id_map.get(c.author_id, c.author_id) == sel_emp]
            )
            tl_df = pd.DataFrame(timeline_rows)
            if not tl_df.empty:
                tl_grp = tl_df.groupby(["월","유형"]).size().reset_index(name="건수")
                fig_tl = px.bar(tl_grp, x="월", y="건수", color="유형",
                                barmode="stack", title="월별 Confluence 활동량")
                st.plotly_chart(fig_tl, use_container_width=True)

        # Cross-space insight
        st.divider()
        spaces_authored = set(p.space_key for p in authored)
        spaces_contrib  = set(p.space_key for p in contributed)
        all_spaces      = spaces_authored | spaces_contrib
        own_space       = SPACE_NAMES.get(
            next((sp for sp, dept in {
                "HR":"인사팀","DEV":"개발팀","MKT":"마케팅팀","FIN":"재무팀","PLAN":"기획팀"
            }.items() if dept == dept), None), "")

        if len(all_spaces) >= 3:
            st.markdown('<div class="insight-box">🌐 이 직원은 <b>여러 스페이스에 걸쳐 활동</b>하는 지식 브릿지 역할을 합니다.</div>', unsafe_allow_html=True)
        elif len(all_spaces) == 1:
            st.markdown('<div class="warn-box">📌 이 직원은 <b>단일 스페이스 집중형</b>으로 부서 내 문서 기여가 주를 이룹니다.</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# TAB 3 — Knowledge Network
# ══════════════════════════════════════════════════════════════════════
with tab_netw:
    st.header("🕸 지식 협업 네트워크")

    net_type = st.radio("네트워크 유형", ["공동편집 네트워크 (무방향)","멘션 네트워크 (방향)"],
                        horizontal=True, key="net_type")

    cn1, cn2 = st.columns([3, 2])

    with cn1:
        if net_type.startswith("공동편집"):
            st.caption("엣지: 같은 페이지를 편집한 직원 쌍 · 두께 = 공동 기여 횟수")
            if not coauthor_df.empty:
                G_ca: nx.Graph = nx.Graph()
                for _, row in coauthor_df.iterrows():
                    a, b, w = row["emp_a"], row["emp_b"], row["공동기여 횟수"]
                    if a != b:
                        if G_ca.has_edge(a, b):
                            G_ca[a][b]["weight"] += w
                        else:
                            G_ca.add_edge(a, b, weight=w)
                for n in G_ca.nodes():
                    G_ca.nodes[n]["dept"] = emp_dept.get(n, "미분류")
                html_ca = _pyvis_html_from_nx(G_ca, directed=False)
                components.html(html_ca, height=580, scrolling=False)
            else:
                st.info("공동편집 데이터 없음")
        else:
            st.caption("화살표: 작성자 → 언급 대상 · 두께 = 멘션 횟수")
            if not mention_df.empty:
                G_mn: nx.DiGraph = nx.DiGraph()
                for _, row in mention_df.iterrows():
                    a, b = row["from_emp"], row["to_emp"]
                    if a != b:
                        if G_mn.has_edge(a, b):
                            G_mn[a][b]["weight"] += 1
                        else:
                            G_mn.add_edge(a, b, weight=1)
                for n in G_mn.nodes():
                    G_mn.nodes[n]["dept"] = emp_dept.get(n, "미분류")
                html_mn = _pyvis_html_from_nx(G_mn, directed=True)
                components.html(html_mn, height=580, scrolling=False)
            else:
                st.info("멘션 데이터 없음")

    with cn2:
        st.subheader("중심성 분석")
        if net_type.startswith("공동편집") and not coauthor_df.empty:
            G_use = G_ca
        elif not net_type.startswith("공동편집") and not mention_df.empty:
            G_use = G_mn
        else:
            G_use = None

        if G_use is not None and G_use.number_of_nodes() > 1:
            deg_c = nx.degree_centrality(G_use)
            bet_c = nx.betweenness_centrality(G_use)
            df_cent = pd.DataFrame({
                "사번": list(deg_c.keys()),
                "연결 중심성": list(deg_c.values()),
                "매개 중심성": [bet_c.get(n,0) for n in deg_c],
            }).sort_values("연결 중심성", ascending=False).head(15)
            df_cent["부서"] = df_cent["사번"].map(emp_dept).fillna("미분류")

            fig_cent = px.bar(df_cent, x="연결 중심성", y="사번", orientation="h",
                              color="부서", title="연결 중심성 TOP 15")
            fig_cent.update_layout(yaxis={"categoryorder":"total ascending"})
            st.plotly_chart(fig_cent, use_container_width=True)

        # Department heatmap
        st.subheader("부서 간 지식 교류 히트맵")
        if not mention_df.empty:
            mention_df["from_dept"] = mention_df["from_emp"].map(emp_dept).fillna("미분류")
            mention_df["to_dept"]   = mention_df["to_emp"].map(emp_dept).fillna("미분류")
            ct = pd.crosstab(mention_df["from_dept"], mention_df["to_dept"])
            fig_ht = px.imshow(ct, text_auto=True, color_continuous_scale="Blues",
                               title="부서 간 멘션 교류", aspect="auto")
            fig_ht.update_layout(height=360)
            st.plotly_chart(fig_ht, use_container_width=True)

    st.divider()
    st.subheader("📋 공동편집 TOP 쌍")
    if not coauthor_df.empty:
        top_co = coauthor_df.sort_values("공동기여 횟수", ascending=False).head(20).copy()
        top_co["A 부서"] = top_co["emp_a"].map(emp_dept).fillna("미분류")
        top_co["B 부서"] = top_co["emp_b"].map(emp_dept).fillna("미분류")
        top_co["같은부서"] = top_co["A 부서"] == top_co["B 부서"]
        st.dataframe(top_co, use_container_width=True, height=300)


# ══════════════════════════════════════════════════════════════════════
# TAB 4 — Comprehensive Talent Profile (4 sources)
# ══════════════════════════════════════════════════════════════════════
with tab_profile:
    st.header("🏆 종합 인재 프로파일 — 4개 소스 통합")
    st.caption("Confluence 지식기여 · 피어리뷰 선정빈도 · 메시지 활성도 · 행사 참여도")

    # Build score DataFrames from each source
    # ── Peer review score ──────────────────────────────────────────
    peer_score: dict[str, float] = {}
    if peer_df is not None and not peer_df.empty:
        rv_col = next((c for c in peer_df.columns if "리뷰어_사번" in c), None)
        rq_col = next((c for c in peer_df.columns if "리뷰대상자_사번" in c), None)
        if rv_col:
            cnt = peer_df[rv_col].value_counts()
            mx = cnt.max() or 1
            peer_score = (cnt / mx * 100).to_dict()

    # ── Message score ──────────────────────────────────────────────
    msg_score: dict[str, float] = {}
    if msg_df is not None and not msg_df.empty:
        s_col = next((c for c in msg_df.columns if "발신자_아이디" in c and "실제" not in c), None)
        r_col = next((c for c in msg_df.columns if "수신자_아이디" in c), None)
        if s_col and r_col:
            send_cnt = msg_df[s_col].value_counts()
            recv_cnt = msg_df[r_col].value_counts()
            all_ids = set(send_cnt.index) | set(recv_cnt.index)
            # msg IDs may be Confluence IDs (사번 in sample)
            raw_score = {i: send_cnt.get(i,0)*0.4 + recv_cnt.get(i,0)*0.6 for i in all_ids}
            mx = max(raw_score.values(), default=1)
            msg_score = {i: v/mx*100 for i, v in raw_score.items()}

    # ── Event participation score ──────────────────────────────────
    event_score: dict[str, float] = {}
    if att_df is not None and not att_df.empty:
        ev_col = next((c for c in att_df.columns if "행사명" in c), None)
        em_col = next((c for c in att_df.columns if "사번" in c), None)
        if ev_col and em_col:
            ev_cnt = att_df.groupby(em_col)[ev_col].count()
            mx = ev_cnt.max() or 1
            event_score = (ev_cnt / mx * 100).to_dict()

    # ── Confluence score ───────────────────────────────────────────
    cf_score_map = activity_df.set_index("사번")["confluence_score"].to_dict() if not activity_df.empty else {}

    # Combine all employees
    all_emp = sorted(set(
        list(cf_score_map.keys()) + list(peer_score.keys()) +
        list(msg_score.keys())   + list(event_score.keys())
    ))

    profile_rows = []
    for emp in all_emp:
        profile_rows.append({
            "사번":           emp,
            "부서":           emp_dept.get(emp, "미분류"),
            "Confluence\n지식기여": round(cf_score_map.get(emp, 0), 1),
            "피어리뷰\n선정빈도":   round(peer_score.get(emp, 0), 1),
            "메시지\n활성도":      round(msg_score.get(emp, 0), 1),
            "행사\n참여도":        round(event_score.get(emp, 0), 1),
        })

    profile_df = pd.DataFrame(profile_rows)
    score_cols = ["Confluence\n지식기여", "피어리뷰\n선정빈도", "메시지\n활성도", "행사\n참여도"]
    profile_df["종합점수"] = profile_df[score_cols].mean(axis=1).round(1)
    profile_df = profile_df.sort_values("종합점수", ascending=False).reset_index(drop=True)

    # ── Employee selector ─────────────────────────────────────────
    sel_prof = st.selectbox("직원 선택", profile_df["사번"].tolist(), key="prof_sel")
    prof_row = profile_df[profile_df["사번"] == sel_prof].iloc[0]

    # Top / department average
    dept_sel = prof_row["부서"]
    dept_avg = profile_df[profile_df["부서"] == dept_sel][score_cols].mean()

    pa, pb = st.columns([2, 3])

    with pa:
        st.subheader(f"📊 {sel_prof} 종합 점수")
        st.metric("종합점수", f"{prof_row['종합점수']:.1f} / 100")
        st.metric("부서 순위",
                  f"{(profile_df[profile_df['부서']==dept_sel]['종합점수'] >= prof_row['종합점수']).sum()} / {(profile_df['부서']==dept_sel).sum()}명")

        for sc in score_cols:
            label = sc.replace("\n", " ")
            val = prof_row[sc]
            dept_v = dept_avg[sc]
            delta = val - dept_v
            st.metric(label, f"{val:.1f}점", f"{delta:+.1f} vs 부서평균")

        # Work style classification
        st.divider()
        st.markdown("**업무 스타일 분류**")
        cf_v  = prof_row["Confluence\n지식기여"]
        pr_v  = prof_row["피어리뷰\n선정빈도"]
        msg_v = prof_row["메시지\n활성도"]
        ev_v  = prof_row["행사\n참여도"]

        styles = []
        if cf_v >= 60:  styles.append("📚 지식 기여자")
        if pr_v >= 60:  styles.append("⭐ 핵심 평가자")
        if msg_v >= 60: styles.append("💬 소통 허브")
        if ev_v >= 60:  styles.append("🎉 행사 참여자")
        if not styles:  styles.append("🔹 균형형")

        for s in styles:
            st.markdown(f'<div class="insight-box">{s}</div>', unsafe_allow_html=True)

    with pb:
        # Radar chart
        radar_cats = ["Confluence\n지식기여", "피어리뷰\n선정빈도", "메시지\n활성도", "행사\n참여도"]
        radar_vals_person = [float(prof_row[c]) for c in radar_cats]
        radar_vals_dept   = [float(dept_avg[c]) for c in radar_cats]
        radar_cats_clean  = [c.replace("\n"," ") for c in radar_cats]

        fig_rad = _radar_chart(
            radar_cats_clean,
            {sel_prof: radar_vals_person, f"{dept_sel} 평균": radar_vals_dept},
        )
        fig_rad.update_layout(title="4개 소스 종합 역량 프로파일")
        st.plotly_chart(fig_rad, use_container_width=True)

    st.divider()

    # Scatter: Confluence vs Peer Review
    st.subheader("📈 전체 직원 분포 분석")
    sa, sb = st.columns(2)

    with sa:
        fig_sc = px.scatter(
            profile_df, x="Confluence\n지식기여", y="피어리뷰\n선정빈도",
            color="부서", size="종합점수", hover_data=["사번","종합점수"],
            title="지식기여 vs 피어리뷰 선정빈도",
            labels={"Confluence\n지식기여":"Confluence 점수","피어리뷰\n선정빈도":"피어리뷰 점수"},
        )
        st.plotly_chart(fig_sc, use_container_width=True)

    with sb:
        fig_sc2 = px.scatter(
            profile_df, x="메시지\n활성도", y="행사\n참여도",
            color="부서", size="종합점수", hover_data=["사번","종합점수"],
            title="메시지 활성도 vs 행사 참여도",
            labels={"메시지\n활성도":"메시지 점수","행사\n참여도":"행사 점수"},
        )
        st.plotly_chart(fig_sc2, use_container_width=True)

    st.subheader("🏅 종합점수 TOP 20")
    top20 = profile_df.head(20).copy()
    top20.columns = [c.replace("\n"," ") for c in top20.columns]
    score_cols_clean = [c.replace("\n"," ") for c in score_cols]
    fig_top = px.bar(
        top20, x="종합점수", y="사번", orientation="h", color="부서",
        title="종합 인재 점수 TOP 20",
        hover_data=score_cols_clean,
    )
    fig_top.update_layout(yaxis={"categoryorder":"total ascending"}, height=520)
    st.plotly_chart(fig_top, use_container_width=True)

    st.divider()
    st.subheader("📋 전체 직원 종합 프로파일 테이블")
    disp_df = profile_df.copy()
    disp_df.columns = [c.replace("\n"," ") for c in disp_df.columns]
    st.dataframe(disp_df, use_container_width=True, height=380)
    csv_prof = disp_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 종합 프로파일 CSV 다운로드", csv_prof, "talent_profile.csv", "text/csv")

    # Heatmap: Employee × Score dimension
    st.divider()
    st.subheader("🗺 임직원 × 역량차원 히트맵")
    top_n_heat = st.slider("표시 인원 수", 10, min(50, len(profile_df)), 25)
    heat_df = profile_df.head(top_n_heat).set_index("사번")[score_cols]
    heat_df.columns = [c.replace("\n"," ") for c in heat_df.columns]
    fig_heat = px.imshow(heat_df, text_auto=".0f",
                         color_continuous_scale="RdYlGn", aspect="auto",
                         title=f"임직원 역량 히트맵 (TOP {top_n_heat})")
    fig_heat.update_layout(height=max(300, top_n_heat * 18))
    st.plotly_chart(fig_heat, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════
# TAB 5 — Department Comparison
# ══════════════════════════════════════════════════════════════════════
with tab_dept:
    st.header("📈 부서별 Confluence 활동 비교")

    if activity_df.empty or "부서" not in activity_df.columns:
        st.info("데이터 없음")
    else:
        dept_agg = activity_df.groupby("부서").agg(
            인원=("사번","count"),
            평균_작성페이지=("작성 페이지","mean"),
            평균_기여페이지=("기여 페이지","mean"),
            평균_댓글=("댓글 작성","mean"),
            평균_조회수=("누적 조회수","mean"),
            평균_멘션수신=("멘션 수신","mean"),
            평균_스페이스수=("활동 스페이스 수","mean"),
            평균_CF점수=("confluence_score","mean"),
        ).round(1).reset_index()

        da, db = st.columns(2)

        with da:
            fig_d1 = px.bar(dept_agg, x="부서", y="평균_CF점수", color="부서",
                            title="부서별 평균 Confluence 점수",
                            color_discrete_sequence=px.colors.qualitative.Set2)
            st.plotly_chart(fig_d1, use_container_width=True)

            fig_d2 = px.bar(dept_agg, x="부서",
                            y=["평균_작성페이지","평균_기여페이지","평균_댓글"],
                            barmode="group", title="부서별 평균 활동 지표",
                            color_discrete_sequence=["#4472C4","#ED7D31","#2ECC71"])
            st.plotly_chart(fig_d2, use_container_width=True)

        with db:
            fig_d3 = px.scatter(dept_agg, x="평균_작성페이지", y="평균_조회수",
                                size="인원", color="부서", text="부서",
                                title="문서 생산성 vs 조회 영향력",
                                labels={"평균_작성페이지":"평균 작성 페이지","평균_조회수":"평균 조회수"})
            fig_d3.update_traces(textposition="top center")
            st.plotly_chart(fig_d3, use_container_width=True)

            fig_d4 = px.bar(dept_agg, x="부서", y="평균_스페이스수",
                            color="부서", title="부서별 평균 활동 스페이스 수 (지식 다양성)",
                            color_discrete_sequence=px.colors.qualitative.Set3)
            st.plotly_chart(fig_d4, use_container_width=True)

        st.divider()
        st.subheader("부서별 종합 프로파일 레이더")
        depts = dept_agg["부서"].tolist()
        if len(depts) >= 2:
            radar_cats_d = ["평균_작성페이지","평균_기여페이지","평균_댓글","평균_멘션수신","평균_CF점수"]
            max_vals_d   = {c: dept_agg[c].max() or 1 for c in radar_cats_d}
            vals_map_d   = {}
            for _, row in dept_agg.iterrows():
                norm = [row[c]/max_vals_d[c]*100 for c in radar_cats_d]
                vals_map_d[row["부서"]] = norm
            cats_clean = [c.replace("평균_","") for c in radar_cats_d]
            fig_rad_d = _radar_chart(cats_clean, vals_map_d)
            fig_rad_d.update_layout(title="부서별 Confluence 역량 프로파일 (정규화)", height=450)
            st.plotly_chart(fig_rad_d, use_container_width=True)

        st.subheader("📋 부서별 요약 테이블")
        st.dataframe(dept_agg, use_container_width=True)
        csv_dept = dept_agg.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 부서 요약 CSV", csv_dept, "dept_confluence_summary.csv", "text/csv")
