import io
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.network_analyzer import (
    GroupingConfig,
    build_centrality_bar_df,
    build_network,
    build_pyvis_html,
    compute_cross_analysis,
    detect_column_mapping,
    generate_sample_attendance,
    generate_sample_peerreview,
    group_by_time_window,
    parse_attendance_dataframe,
    parse_peerreview_dataframe,
    run_analysis,
)

st.set_page_config(page_title="행사 네트워크 분석", page_icon="🔗", layout="wide")

st.title("🔗 직원 친밀도 네트워크 분석")
st.caption(
    "**가설 1** 행사에서 같은 시간대에 태깅한 사람들은 서로 친할 것이다 · "
    "**가설 2** 피어리뷰 동료로 지정한 사람은 친밀한 관계일 것이다"
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("📂 데이터 업로드")

    use_sample = st.button("샘플 데이터 사용", use_container_width=True)
    st.divider()

    att_file = st.file_uploader("행사 데이터 (CSV / Excel)", type=["csv", "xlsx", "xls"], key="att_file")
    pr_file = st.file_uploader("피어리뷰 데이터 (선택, CSV / Excel)", type=["csv", "xlsx", "xls"], key="pr_file")

    st.divider()
    st.header("⚙️ 분석 설정")
    threshold = st.slider("시간 임계값 (초)", min_value=1, max_value=60, value=5, step=1,
                          help="이 초 이내에 연속 태깅한 사람들을 같은 그룹으로 봅니다")
    top_n = st.slider("상위 N명 표시", min_value=5, max_value=50, value=20, step=5)

    run_btn = st.button("🚀 분석 실행", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Load / prepare raw DataFrames
# ---------------------------------------------------------------------------

def _read_file(uploaded) -> pd.DataFrame:
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded)
    return pd.read_excel(uploaded)


if use_sample:
    att_df = generate_sample_attendance()
    pr_df = generate_sample_peerreview(att_df)
    st.session_state["att_df"] = att_df
    st.session_state["pr_df"] = pr_df
    st.session_state["result"] = None
    st.success("샘플 데이터가 로드되었습니다. 아래에서 컬럼 매핑을 확인하고 분석을 실행하세요.")

if att_file:
    st.session_state["att_df"] = _read_file(att_file)
    st.session_state["result"] = None

if pr_file:
    st.session_state["pr_df"] = _read_file(pr_file)
    st.session_state["result"] = None

att_df: pd.DataFrame = st.session_state.get("att_df")
pr_df: pd.DataFrame = st.session_state.get("pr_df")

# ---------------------------------------------------------------------------
# Column mapping UI (shown in sidebar after data load)
# ---------------------------------------------------------------------------

att_col_map: dict = {}
pr_col_map: dict = {}

if att_df is not None:
    with st.sidebar:
        st.divider()
        st.header("🗂️ 행사 데이터 컬럼 매핑")
        auto_att = detect_column_mapping(att_df, schema="attendance")
        cols = list(att_df.columns)
        _none_opt = ["(없음)"] + cols

        def _sel(label, key_name, auto):
            default = auto.get(key_name, "(없음)")
            idx = _none_opt.index(default) if default in _none_opt else 0
            return st.selectbox(label, _none_opt, index=idx, key=f"att_{key_name}")

        att_col_map = {
            "employee_id": _sel("사번 *", "employee_id", auto_att),
            "name":        _sel("이름", "name", auto_att),
            "event_id":    _sel("행사명 *", "event_id", auto_att),
            "tag_time":    _sel("태깅시간 *", "tag_time", auto_att),
            "department":  _sel("부서", "department", auto_att),
            "position":    _sel("직책", "position", auto_att),
        }
        # Remove "(없음)" entries
        att_col_map = {k: v for k, v in att_col_map.items() if v != "(없음)"}

if pr_df is not None:
    with st.sidebar:
        st.divider()
        st.header("🗂️ 피어리뷰 컬럼 매핑")
        auto_pr = detect_column_mapping(pr_df, schema="peerreview")
        pr_cols = list(pr_df.columns)
        _pr_none = ["(없음)"] + pr_cols

        def _pr_sel(label, key_name, auto):
            default = auto.get(key_name, "(없음)")
            idx = _pr_none.index(default) if default in _pr_none else 0
            return st.selectbox(label, _pr_none, index=idx, key=f"pr_{key_name}")

        pr_col_map = {
            "requester_id":   _pr_sel("요청자 사번 *", "requester_id", auto_pr),
            "requester_dept": _pr_sel("요청자 부서", "requester_dept", auto_pr),
            "reviewer_id":    _pr_sel("리뷰어 사번 *", "reviewer_id", auto_pr),
            "reviewer_dept":  _pr_sel("리뷰어 부서", "reviewer_dept", auto_pr),
        }
        pr_col_map = {k: v for k, v in pr_col_map.items() if v != "(없음)"}

# ---------------------------------------------------------------------------
# Run analysis
# ---------------------------------------------------------------------------

if run_btn:
    if att_df is None:
        st.sidebar.error("행사 데이터를 업로드하거나 샘플 데이터를 사용하세요.")
    else:
        with st.spinner("분석 중..."):
            try:
                records, att_warns = parse_attendance_dataframe(att_df, att_col_map)
                if att_warns:
                    for w in att_warns:
                        st.warning(w)

                peer_records = []
                pr_warns = []
                if pr_df is not None and pr_col_map:
                    peer_records, pr_warns = parse_peerreview_dataframe(pr_df, pr_col_map)
                    if pr_warns:
                        for w in pr_warns:
                            st.warning(w)

                config = GroupingConfig(time_threshold_seconds=threshold)
                event_groups = group_by_time_window(records, config)

                # Warn about very large groups
                for ev, groups in event_groups.items():
                    for g in groups:
                        if len(g) > 50:
                            st.warning(f"행사 '{ev}': 그룹 크기 {len(g)}명 — 임계값이 너무 크지 않은지 확인하세요.")

                G = build_network(event_groups, peer_records if peer_records else None)

                if G.number_of_nodes() == 0:
                    st.error("분석할 직원 데이터가 없습니다.")
                else:
                    result = run_analysis(G)
                    cross = compute_cross_analysis(event_groups, peer_records) if peer_records else None

                    st.session_state["result"] = result
                    st.session_state["event_groups"] = event_groups
                    st.session_state["peer_records"] = peer_records
                    st.session_state["cross"] = cross
                    st.session_state["threshold"] = threshold
                    st.session_state["top_n"] = top_n
                    st.success(f"분석 완료 — 직원 {G.number_of_nodes()}명, 연결 {G.number_of_edges()}쌍")

            except ValueError as e:
                st.error(str(e))

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab1, tab2, tab3, tab4 = st.tabs(["📋 데이터 미리보기", "🕸️ 네트워크 그래프", "📊 중심성 분석", "📈 요약 통계"])

# ── Tab 1: Data preview ──────────────────────────────────────────────────────
with tab1:
    if att_df is None:
        st.info("왼쪽 사이드바에서 데이터를 업로드하거나 샘플 데이터를 사용하세요.")
    else:
        st.subheader("행사 데이터")
        st.dataframe(att_df, use_container_width=True, height=300)
        st.caption(f"행: {len(att_df):,}  컬럼: {list(att_df.columns)}")

        if att_col_map:
            st.subheader("컬럼 매핑 확인")
            mapping_display = {k: v for k, v in att_col_map.items()}
            st.json(mapping_display)

    if pr_df is not None:
        st.subheader("피어리뷰 데이터")
        st.dataframe(pr_df, use_container_width=True, height=300)
        st.caption(f"행: {len(pr_df):,}  컬럼: {list(pr_df.columns)}")

# ── Tab 2: Network graph ──────────────────────────────────────────────────────
with tab2:
    result = st.session_state.get("result")
    if result is None:
        st.info("분석을 먼저 실행하세요.")
    else:
        col_a, col_b, col_c = st.columns([2, 2, 3])
        with col_a:
            color_by = st.radio("노드 색상 기준", ["부서", "커뮤니티"], horizontal=True, key="color_by")
        with col_b:
            color_by_key = "department" if color_by == "부서" else "community"

        # Edge source legend
        with col_c:
            stats = result.edge_source_stats
            st.markdown(
                f"🔵 행사만: **{stats.get('event', 0)}** 쌍 &nbsp;|&nbsp; "
                f"🟠 리뷰만: **{stats.get('review', 0)}** 쌍 &nbsp;|&nbsp; "
                f"🟢 양쪽: **{stats.get('both', 0)}** 쌍"
            )

        html = build_pyvis_html(result.graph, result, color_by=color_by_key)
        components.html(html, height=680, scrolling=False)

        st.caption("노드 크기 = 연결 중심성 | 엣지 굵기 = 연결 강도 | 드래그·줌·클릭 가능")

# ── Tab 3: Centrality analysis ────────────────────────────────────────────────
with tab3:
    result = st.session_state.get("result")
    if result is None:
        st.info("분석을 먼저 실행하세요.")
    else:
        import plotly.express as px

        metric_label = st.radio(
            "분석 지표",
            ["연결 중심성 (Degree)", "매개 중심성 (Betweenness)", "근접 중심성 (Closeness)"],
            horizontal=True,
        )
        metric_key = {"연결 중심성 (Degree)": "degree", "매개 중심성 (Betweenness)": "betweenness", "근접 중심성 (Closeness)": "closeness"}[metric_label]

        tn = st.session_state.get("top_n", 20)
        bar_df = build_centrality_bar_df(result, metric=metric_key, top_n=tn)

        fig = px.bar(
            bar_df,
            x="점수", y="이름",
            orientation="h",
            color="부서",
            hover_data=["사번", "부서", "점수"],
            title=f"Top {tn} — {metric_label}",
            labels={"점수": "중심성 점수", "이름": ""},
            height=max(400, tn * 28),
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, legend_title="부서")
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("데이터 보기"):
            st.dataframe(bar_df, use_container_width=True)

        # Metric explanations
        with st.expander("📖 중심성 지표 설명"):
            st.markdown("""
| 지표 | 의미 |
|------|------|
| **연결 중심성 (Degree)** | 직접 연결된 사람 수. 값이 높을수록 많은 사람과 연결되어 있음 |
| **매개 중심성 (Betweenness)** | 다른 쌍 간 최단 경로에 얼마나 자주 등장하는지. 정보 전달 허브 역할 |
| **근접 중심성 (Closeness)** | 다른 모든 노드까지의 평균 거리 역수. 네트워크 전체에 빠르게 도달 가능 |
""")

# ── Tab 4: Summary stats ──────────────────────────────────────────────────────
with tab4:
    result = st.session_state.get("result")
    if result is None:
        st.info("분석을 먼저 실행하세요.")
    else:
        import plotly.express as px

        # Summary table
        st.subheader("직원별 중심성 요약")
        st.dataframe(result.summary_df, use_container_width=True, height=350)

        # Download button
        csv_bytes = result.summary_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 결과 CSV 다운로드", csv_bytes, "network_summary.csv", "text/csv")

        st.divider()

        # Dept heatmap
        st.subheader("부서 간 연결 강도 히트맵")
        if not result.dept_heatmap_df.empty:
            fig_heat = px.imshow(
                result.dept_heatmap_df,
                text_auto=True,
                color_continuous_scale="Blues",
                title="부서 × 부서 연결 강도 (엣지 weight 합계)",
                labels={"color": "연결 강도"},
            )
            fig_heat.update_layout(height=450)
            st.plotly_chart(fig_heat, use_container_width=True)

        st.divider()

        # Edge ratio
        st.subheader("엣지 유형 분포")
        G = result.graph
        total_edges = G.number_of_edges()
        if total_edges > 0:
            same_dept = sum(
                1 for u, v in G.edges()
                if G.nodes[u].get("department") == G.nodes[v].get("department")
            )
            cross_dept = total_edges - same_dept
            c1, c2, c3 = st.columns(3)
            c1.metric("전체 연결 쌍", f"{total_edges:,}")
            c2.metric("같은 부서 내", f"{same_dept:,}", f"{same_dept/total_edges*100:.1f}%")
            c3.metric("부서 간 연결", f"{cross_dept:,}", f"{cross_dept/total_edges*100:.1f}%")

        st.divider()

        # Cross-analysis
        cross = st.session_state.get("cross")
        if cross:
            st.subheader("🔬 교차 분석: 두 가설의 일치도")
            st.markdown(
                f"행사에서 같은 그룹이었던 **{cross['행사 기반 쌍 수']}쌍** 중 "
                f"피어리뷰로도 지정한 쌍은 **{cross['양쪽 모두 해당 쌍 수']}쌍**입니다."
            )

            ca, cb, cc, cd = st.columns(4)
            ca.metric("행사 기반 쌍", f"{cross['행사 기반 쌍 수']:,}")
            cb.metric("피어리뷰 기반 쌍", f"{cross['피어리뷰 기반 쌍 수']:,}")
            cc.metric("양쪽 일치 쌍", f"{cross['양쪽 모두 해당 쌍 수']:,}")
            cd.metric("교차 비율", f"{cross['교차 비율 (%)']}%",
                      help="행사 그룹 쌍 중 피어리뷰도 지정한 비율")

            st.info(
                f"교차 비율 {cross['교차 비율 (%)']}% — "
                "높을수록 두 가설이 서로를 뒷받침하며 행사 공동참여가 피어리뷰 선택과 연관됩니다."
            )
        elif st.session_state.get("peer_records") is not None and len(st.session_state["peer_records"]) == 0:
            st.info("피어리뷰 데이터가 없어 교차 분석을 수행할 수 없습니다.")
