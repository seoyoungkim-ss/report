from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional
import random

import networkx as nx
import pandas as pd

try:
    import community as community_louvain
    _LOUVAIN_AVAILABLE = True
except ImportError:
    _LOUVAIN_AVAILABLE = False

# networkx built-in community detection (fallback)
from networkx.algorithms import community as nx_community


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EmployeeRecord:
    employee_id: str
    name: str
    department: str
    position: str
    event_id: str
    tag_time: float


@dataclass
class PeerReviewRecord:
    requester_id: str
    requester_dept: str
    reviewer_id: str
    reviewer_dept: str
    collab_frequency: str = ""   # 주 1회 미만 / 주 1회 / 주 2~3회 / 주 4~5회 / 기타
    assignment_method: str = ""  # 협업동료 / 과제내 / 부서내 / 랜덤배정


@dataclass
class MessageRecord:
    sender_id: str
    receiver_id: str
    sender_dept: str
    receiver_dept: str
    send_dt: pd.Timestamp
    letter_type: str   # 감사 / 응원 / 안부 / 협업 / 기념일
    keywords: list[str]
    is_anonymous: bool


@dataclass
class GroupingConfig:
    time_threshold_seconds: float = 5.0


@dataclass
class NetworkAnalysisResult:
    graph: nx.Graph
    degree_centrality: dict
    betweenness_centrality: dict
    closeness_centrality: dict
    community_map: dict
    summary_df: pd.DataFrame
    dept_heatmap_df: pd.DataFrame
    edge_source_stats: dict


# ---------------------------------------------------------------------------
# Column mapping heuristics
# ---------------------------------------------------------------------------

_ATTENDANCE_SCHEMA = {
    "employee_id": ["사번", "직원번호", "employee_id", "emp_id", "id"],
    "name":        ["이름", "성명", "name", "직원명"],
    "event_id":    ["행사명", "행사id", "행사", "event", "event_id", "event_name"],
    "tag_time":    ["태깅시간", "tagging", "time", "timestamp", "시간", "태깅 시간"],
    "department":  ["부서", "부서명", "department", "dept", "org"],
    "position":    ["직책", "직위", "position", "title", "rank"],
}

_PEERREVIEW_SCHEMA = {
    "requester_id":      ["요청자_사번", "요청자사번", "리뷰요청자사번", "리뷰대상자_사번", "requester_id", "from_id", "평가자사번"],
    "requester_dept":    ["요청자_부서", "요청자부서", "리뷰대상자_부서", "requester_dept", "from_dept"],
    "reviewer_id":       ["리뷰어_사번", "리뷰어사번", "reviewer_id", "to_id", "동료사번", "피평가자사번"],
    "reviewer_dept":     ["리뷰어_부서", "리뷰어부서", "reviewer_dept", "to_dept"],
    "collab_frequency":  ["협업빈도", "추천사유_협업빈도", "추천사유", "협업_빈도", "collab_frequency", "frequency"],
    "assignment_method": ["배정방식", "assignment_method", "배정유형", "선정방식"],
}

_MESSAGE_SCHEMA = {
    "sender_id":    ["발신자_아이디", "발신자아이디", "발신자id", "sender_id", "from_id", "sender"],
    "receiver_id":  ["수신자_아이디", "수신자아이디", "수신자id", "receiver_id", "to_id", "receiver"],
    "sender_dept":  ["발신자_부서", "발신자부서", "sender_dept", "from_dept"],
    "receiver_dept":["수신자_부서", "수신자부서", "receiver_dept", "to_dept"],
    "send_dt":      ["발송일시", "발송일", "send_dt", "sent_at", "timestamp", "날짜", "일시"],
    "letter_type":  ["레터유형", "레터_유형", "letter_type", "type", "유형"],
    "keywords":     ["키워드", "keywords", "keyword", "태그"],
    "is_anonymous": ["익명여부", "익명", "anonymous", "is_anonymous"],
}


def detect_column_mapping(df: pd.DataFrame, schema: str = "attendance") -> dict[str, str]:
    """schema: 'attendance' | 'peerreview' | 'message'"""
    """Heuristically map canonical field names to actual column headers."""
    mapping_schema = {"attendance": _ATTENDANCE_SCHEMA, "peerreview": _PEERREVIEW_SCHEMA, "message": _MESSAGE_SCHEMA}.get(schema, _ATTENDANCE_SCHEMA)
    columns_lower = {c.lower().strip(): c for c in df.columns}
    result = {}
    for canonical, keywords in mapping_schema.items():
        for kw in keywords:
            if kw.lower() in columns_lower:
                result[canonical] = columns_lower[kw.lower()]
                break
        else:
            # partial match fallback
            for kw in keywords:
                for col_lower, col_orig in columns_lower.items():
                    if kw.lower() in col_lower:
                        result[canonical] = col_orig
                        break
                if canonical in result:
                    break
    return result


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_attendance_dataframe(
    df: pd.DataFrame,
    col_map: dict[str, str],
) -> tuple[list[EmployeeRecord], list[str]]:
    """
    Convert raw DataFrame to EmployeeRecord list.
    Returns (records, warnings).
    """
    required = ["employee_id", "event_id", "tag_time"]
    for key in required:
        if key not in col_map or col_map[key] not in df.columns:
            raise ValueError(f"필수 컬럼 '{key}'을(를) 찾을 수 없습니다. 컬럼 매핑을 확인하세요.")

    warnings = []
    records = []

    for _, row in df.iterrows():
        emp_id = str(row[col_map["employee_id"]]).strip()
        name = str(row[col_map.get("name", "")]).strip() if col_map.get("name") and col_map["name"] in df.columns else emp_id
        dept = str(row[col_map.get("department", "")]).strip() if col_map.get("department") and col_map["department"] in df.columns else "미분류"
        position = str(row[col_map.get("position", "")]).strip() if col_map.get("position") and col_map["position"] in df.columns else "미기재"
        event_id = str(row[col_map["event_id"]]).strip()
        raw_time = row[col_map["tag_time"]]

        if not emp_id or emp_id in ("nan", "None", ""):
            continue

        tag_time = _parse_time(raw_time)
        if tag_time is None:
            warnings.append(f"사번 {emp_id}: 태깅시간 '{raw_time}' 파싱 실패, 건너뜀")
            continue

        if not name or name in ("nan", "None"):
            name = emp_id
        if not dept or dept in ("nan", "None"):
            dept = "미분류"
        if not position or position in ("nan", "None"):
            position = "미기재"

        records.append(EmployeeRecord(emp_id, name, dept, position, event_id, tag_time))

    # Deduplicate by (employee_id, event_id) — keep earliest tag_time
    seen: dict[tuple, EmployeeRecord] = {}
    for r in records:
        key = (r.employee_id, r.event_id)
        if key not in seen or r.tag_time < seen[key].tag_time:
            seen[key] = r
        elif key in seen:
            warnings.append(f"사번 {r.employee_id} 행사 '{r.event_id}' 중복 태깅 감지, 이른 시간 유지")
    return list(seen.values()), warnings


def parse_peerreview_dataframe(
    df: pd.DataFrame,
    col_map: dict[str, str],
) -> tuple[list[PeerReviewRecord], list[str]]:
    """Convert raw peer-review DataFrame to PeerReviewRecord list."""
    required = ["requester_id", "reviewer_id"]
    for key in required:
        if key not in col_map or col_map[key] not in df.columns:
            raise ValueError(f"필수 컬럼 '{key}'을(를) 찾을 수 없습니다. 컬럼 매핑을 확인하세요.")

    warnings = []
    records = []
    for _, row in df.iterrows():
        req_id = str(row[col_map["requester_id"]]).strip()
        rev_id = str(row[col_map["reviewer_id"]]).strip()
        req_dept = str(row[col_map.get("requester_dept", "")]).strip() if col_map.get("requester_dept") and col_map["requester_dept"] in df.columns else "미분류"
        rev_dept = str(row[col_map.get("reviewer_dept", "")]).strip() if col_map.get("reviewer_dept") and col_map["reviewer_dept"] in df.columns else "미분류"

        if not req_id or req_id in ("nan", "None") or not rev_id or rev_id in ("nan", "None"):
            continue
        if req_id == rev_id:
            warnings.append(f"사번 {req_id}: 자기 자신을 리뷰어로 지정, 건너뜀")
            continue

        if req_dept in ("nan", "None"):
            req_dept = "미분류"
        if rev_dept in ("nan", "None"):
            rev_dept = "미분류"

        freq = ""
        if col_map.get("collab_frequency") and col_map["collab_frequency"] in df.columns:
            freq = str(row[col_map["collab_frequency"]]).strip()
            if freq in ("nan", "None"):
                freq = ""

        method = ""
        if col_map.get("assignment_method") and col_map["assignment_method"] in df.columns:
            method = str(row[col_map["assignment_method"]]).strip()
            if method in ("nan", "None"):
                method = ""

        records.append(PeerReviewRecord(req_id, req_dept, rev_id, rev_dept, freq, method))
    return records, warnings


# ---------------------------------------------------------------------------
# Message parser
# ---------------------------------------------------------------------------

def parse_message_dataframe(
    df: pd.DataFrame,
    col_map: dict[str, str],
) -> tuple[list[MessageRecord], list[str]]:
    """Convert raw message-platform DataFrame to MessageRecord list."""
    required = ["sender_id", "receiver_id"]
    for key in required:
        if key not in col_map or col_map[key] not in df.columns:
            raise ValueError(f"메시지 데이터 필수 컬럼 '{key}'을(를) 찾을 수 없습니다.")

    warnings: list[str] = []
    records: list[MessageRecord] = []

    for _, row in df.iterrows():
        sender = str(row[col_map["sender_id"]]).strip()
        receiver = str(row[col_map["receiver_id"]]).strip()
        if not sender or sender in ("nan", "None") or not receiver or receiver in ("nan", "None"):
            continue
        if sender == receiver:
            continue

        sender_dept = "미분류"
        if col_map.get("sender_dept") and col_map["sender_dept"] in df.columns:
            v = str(row[col_map["sender_dept"]]).strip()
            sender_dept = v if v not in ("nan", "None", "") else "미분류"

        receiver_dept = "미분류"
        if col_map.get("receiver_dept") and col_map["receiver_dept"] in df.columns:
            v = str(row[col_map["receiver_dept"]]).strip()
            receiver_dept = v if v not in ("nan", "None", "") else "미분류"

        send_dt = pd.NaT
        if col_map.get("send_dt") and col_map["send_dt"] in df.columns:
            try:
                send_dt = pd.to_datetime(row[col_map["send_dt"]])
            except Exception:
                pass

        letter_type = ""
        if col_map.get("letter_type") and col_map["letter_type"] in df.columns:
            letter_type = str(row[col_map["letter_type"]]).strip()
            if letter_type in ("nan", "None"):
                letter_type = ""

        kw_raw = ""
        if col_map.get("keywords") and col_map["keywords"] in df.columns:
            kw_raw = str(row[col_map["keywords"]]).strip()
        keywords = [k.strip() for k in kw_raw.split(",") if k.strip() and k.strip() not in ("nan", "None")]

        is_anon = False
        if col_map.get("is_anonymous") and col_map["is_anonymous"] in df.columns:
            v = str(row[col_map["is_anonymous"]]).strip().upper()
            is_anon = v in ("Y", "YES", "TRUE", "1", "익명")

        records.append(MessageRecord(sender, receiver, sender_dept, receiver_dept, send_dt, letter_type, keywords, is_anon))

    return records, warnings


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def group_by_time_window(
    records: list[EmployeeRecord],
    config: GroupingConfig,
) -> dict[str, list[list[EmployeeRecord]]]:
    """
    Gap-based grouping: split into new group when consecutive gap > threshold.
    Returns {event_id: [[group0_members], [group1_members], ...]}.
    """
    from collections import defaultdict
    by_event: dict[str, list[EmployeeRecord]] = defaultdict(list)
    for r in records:
        by_event[r.event_id].append(r)

    result = {}
    for event_id, ev_records in by_event.items():
        sorted_recs = sorted(ev_records, key=lambda r: r.tag_time)
        if len(sorted_recs) == 1:
            result[event_id] = [[sorted_recs[0]]]
            continue

        groups: list[list[EmployeeRecord]] = []
        current: list[EmployeeRecord] = [sorted_recs[0]]
        for i in range(1, len(sorted_recs)):
            gap = sorted_recs[i].tag_time - sorted_recs[i - 1].tag_time
            if gap <= config.time_threshold_seconds:
                current.append(sorted_recs[i])
            else:
                groups.append(current)
                current = [sorted_recs[i]]
        groups.append(current)
        result[event_id] = groups
    return result


# ---------------------------------------------------------------------------
# Network builder
# ---------------------------------------------------------------------------

def build_network(
    event_groups: dict[str, list[list[EmployeeRecord]]],
    peer_reviews: Optional[list[PeerReviewRecord]] = None,
    attendance_records: Optional[list[EmployeeRecord]] = None,
) -> nx.Graph:
    """
    Build a weighted undirected graph.
    Edge attributes: weight, event_weight, review_weight, source.
    """
    G = nx.Graph()

    # Build a lookup: employee_id → (name, department, position)
    emp_info: dict[str, dict] = {}
    for groups in event_groups.values():
        for group in groups:
            for r in group:
                emp_info[r.employee_id] = {
                    "name": r.name,
                    "department": r.department,
                    "position": r.position,
                }

    # Supplement from peer review department info
    if peer_reviews:
        for pr in peer_reviews:
            if pr.requester_id not in emp_info:
                emp_info[pr.requester_id] = {"name": pr.requester_id, "department": pr.requester_dept, "position": "미기재"}
            if pr.reviewer_id not in emp_info:
                emp_info[pr.reviewer_id] = {"name": pr.reviewer_id, "department": pr.reviewer_dept, "position": "미기재"}

    # Add all nodes
    for emp_id, attrs in emp_info.items():
        G.add_node(emp_id, **attrs)

    def _get_or_create_edge(u: str, v: str):
        if not G.has_edge(u, v):
            G.add_edge(u, v, weight=0, event_weight=0, review_weight=0, source="none")
        return G[u][v]

    # Event edges
    for groups in event_groups.values():
        for group in groups:
            if len(group) < 2:
                continue
            for a, b in combinations(group, 2):
                e = _get_or_create_edge(a.employee_id, b.employee_id)
                e["event_weight"] += 1
                e["weight"] += 1

    # Peer review edges
    if peer_reviews:
        for pr in peer_reviews:
            e = _get_or_create_edge(pr.requester_id, pr.reviewer_id)
            e["review_weight"] += 1
            e["weight"] += 1

    # Set source label
    for u, v, data in G.edges(data=True):
        if data["event_weight"] > 0 and data["review_weight"] > 0:
            data["source"] = "both"
        elif data["event_weight"] > 0:
            data["source"] = "event"
        else:
            data["source"] = "review"

    return G


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def run_analysis(graph: nx.Graph) -> NetworkAnalysisResult:
    """Compute centrality metrics, communities, and summary DataFrames."""
    G = graph

    degree_c = nx.degree_centrality(G)
    between_c = nx.betweenness_centrality(G, normalized=True)
    if G.number_of_nodes() > 1:
        close_c = nx.closeness_centrality(G)
    else:
        close_c = {n: 0.0 for n in G.nodes()}

    # Community detection
    community_map: dict[str, int] = {}
    if G.number_of_edges() > 0:
        if _LOUVAIN_AVAILABLE:
            community_map = community_louvain.best_partition(G, weight="weight")
        else:
            # Greedy modularity (networkx built-in, O(n log n))
            comms = nx_community.greedy_modularity_communities(G, weight="weight")
            for comm_id, members in enumerate(comms):
                for node in members:
                    community_map[node] = comm_id
    if not community_map:
        for n in G.nodes():
            community_map[n] = 0

    # Summary DataFrame
    rows = []
    for node in G.nodes():
        attrs = G.nodes[node]
        rows.append({
            "사번": node,
            "이름": attrs.get("name", node),
            "부서": attrs.get("department", "미분류"),
            "직책": attrs.get("position", "미기재"),
            "커뮤니티": community_map.get(node, 0),
            "연결 중심성": round(degree_c.get(node, 0), 4),
            "매개 중심성": round(between_c.get(node, 0), 4),
            "근접 중심성": round(close_c.get(node, 0), 4),
            "연결 수": G.degree(node),
        })
    summary_df = pd.DataFrame(rows).sort_values("연결 중심성", ascending=False).reset_index(drop=True)

    # Department heatmap
    dept_heatmap_df = _build_dept_heatmap(G)

    # Edge source stats
    edge_source_stats = {"event": 0, "review": 0, "both": 0}
    for _, _, data in G.edges(data=True):
        src = data.get("source", "none")
        if src in edge_source_stats:
            edge_source_stats[src] += 1

    return NetworkAnalysisResult(
        graph=G,
        degree_centrality=degree_c,
        betweenness_centrality=between_c,
        closeness_centrality=close_c,
        community_map=community_map,
        summary_df=summary_df,
        dept_heatmap_df=dept_heatmap_df,
        edge_source_stats=edge_source_stats,
    )


def _build_dept_heatmap(G: nx.Graph) -> pd.DataFrame:
    depts = sorted({G.nodes[n].get("department", "미분류") for n in G.nodes()})
    matrix = {d: {d2: 0 for d2 in depts} for d in depts}
    for u, v, data in G.edges(data=True):
        d1 = G.nodes[u].get("department", "미분류")
        d2 = G.nodes[v].get("department", "미분류")
        w = data.get("weight", 1)
        matrix[d1][d2] += w
        if d1 != d2:
            matrix[d2][d1] += w
    return pd.DataFrame(matrix, index=depts, columns=depts)


# ---------------------------------------------------------------------------
# Visualization helpers
# ---------------------------------------------------------------------------

def build_pyvis_html(
    graph: nx.Graph,
    result: NetworkAnalysisResult,
    color_by: str = "department",
    top_n_labels: int = 30,
) -> str:
    """
    Return self-contained HTML for pyvis interactive network.
    color_by: "department" | "community"
    """
    from pyvis.network import Network

    net = Network(height="650px", width="100%", bgcolor="#1a1a2e", font_color="#ffffff", notebook=False)
    net.set_options("""
    {
      "physics": {
        "forceAtlas2Based": {
          "gravitationalConstant": -50,
          "centralGravity": 0.01,
          "springLength": 100,
          "springConstant": 0.08
        },
        "maxVelocity": 50,
        "solver": "forceAtlas2Based",
        "timestep": 0.35,
        "stabilization": { "iterations": 150 }
      },
      "edges": {
        "smooth": { "type": "continuous" }
      },
      "interaction": {
        "hover": true,
        "navigationButtons": true,
        "keyboard": true
      }
    }
    """)

    degree_c = result.degree_centrality
    community_map = result.community_map

    # Color palettes
    dept_colors = _assign_colors(
        sorted({graph.nodes[n].get("department", "미분류") for n in graph.nodes()})
    )
    community_colors = _assign_colors(
        sorted(set(community_map.values()))
    )

    # Sort nodes by degree for label visibility control
    sorted_nodes = sorted(graph.nodes(), key=lambda n: degree_c.get(n, 0), reverse=True)

    for i, node in enumerate(sorted_nodes):
        attrs = graph.nodes[node]
        name = attrs.get("name", node)
        dept = attrs.get("department", "미분류")
        pos = attrs.get("position", "미기재")
        dc = round(degree_c.get(node, 0), 3)
        bc = round(result.betweenness_centrality.get(node, 0), 3)
        cc = round(result.closeness_centrality.get(node, 0), 3)
        comm = community_map.get(node, 0)
        deg = graph.degree(node)

        if color_by == "community":
            color = community_colors.get(comm, "#888888")
        else:
            color = dept_colors.get(dept, "#888888")

        size = 10 + dc * 60
        label = name if i < top_n_labels else ""

        tooltip = (
            f"<b>{name}</b> ({node})<br>"
            f"부서: {dept} | 직책: {pos}<br>"
            f"커뮤니티: {comm}<br>"
            f"연결 수: {deg}<br>"
            f"연결 중심성: {dc}<br>"
            f"매개 중심성: {bc}<br>"
            f"근접 중심성: {cc}"
        )
        net.add_node(node, label=label, title=tooltip, color=color, size=size)

    # Edge colors by source
    _edge_color = {"event": "#4a9eff", "review": "#ff8c42", "both": "#2ecc71", "none": "#888888"}

    max_weight = max((d.get("weight", 1) for _, _, d in graph.edges(data=True)), default=1)
    for u, v, data in graph.edges(data=True):
        w = data.get("weight", 1)
        src = data.get("source", "none")
        ew = data.get("event_weight", 0)
        rw = data.get("review_weight", 0)
        width = 1 + (w / max_weight) * 8
        tooltip = f"행사 공동참여: {ew}회 | 피어리뷰 지정: {rw}회"
        net.add_edge(u, v, width=width, color=_edge_color.get(src, "#888888"), title=tooltip)

    return net.generate_html()


def build_centrality_bar_df(
    result: NetworkAnalysisResult,
    metric: str = "degree",
    top_n: int = 20,
) -> pd.DataFrame:
    """Return tidy DataFrame for plotly bar chart."""
    metric_map = {
        "degree": ("연결 중심성", result.degree_centrality),
        "betweenness": ("매개 중심성", result.betweenness_centrality),
        "closeness": ("근접 중심성", result.closeness_centrality),
    }
    label, data = metric_map.get(metric, metric_map["degree"])
    G = result.graph
    rows = [
        {
            "사번": node,
            "이름": G.nodes[node].get("name", node),
            "부서": G.nodes[node].get("department", "미분류"),
            "점수": round(val, 4),
            "지표": label,
        }
        for node, val in data.items()
    ]
    df = pd.DataFrame(rows).sort_values("점수", ascending=False).head(top_n).reset_index(drop=True)
    return df


def compute_cross_analysis(
    event_groups: dict[str, list[list[EmployeeRecord]]],
    peer_reviews: list[PeerReviewRecord],
) -> dict:
    """
    두 가설 교차 분석:
    행사에서 같은 그룹이었던 쌍 중 피어리뷰도 지정한 비율.
    """
    event_pairs: set[frozenset] = set()
    for groups in event_groups.values():
        for group in groups:
            if len(group) < 2:
                continue
            for a, b in combinations(group, 2):
                event_pairs.add(frozenset([a.employee_id, b.employee_id]))

    review_pairs: set[frozenset] = {
        frozenset([pr.requester_id, pr.reviewer_id]) for pr in peer_reviews
    }

    overlap = event_pairs & review_pairs
    total_event_pairs = len(event_pairs)
    overlap_count = len(overlap)
    ratio = overlap_count / total_event_pairs if total_event_pairs > 0 else 0.0

    return {
        "행사 기반 쌍 수": total_event_pairs,
        "피어리뷰 기반 쌍 수": len(review_pairs),
        "양쪽 모두 해당 쌍 수": overlap_count,
        "교차 비율 (%)": round(ratio * 100, 2),
    }


# ---------------------------------------------------------------------------
# Sample data generator
# ---------------------------------------------------------------------------

def generate_sample_attendance() -> pd.DataFrame:
    """Generate realistic sample attendance data for testing."""
    random.seed(42)
    departments = ["개발팀", "마케팅팀", "인사팀", "재무팀", "기획팀"]
    positions = ["팀장", "선임", "대리", "사원"]
    events = ["행사A", "행사B", "행사C"]

    employees = []
    for i in range(1, 31):
        emp_id = f"EMP{i:03d}"
        name = f"직원{i:02d}"
        dept = departments[(i - 1) % len(departments)]
        pos = positions[(i - 1) % len(positions)]
        employees.append((emp_id, name, dept, pos))

    rows = []
    for event in events:
        base_time = 0
        # Create groups of 2~5 people, plus 1-2 solo arrivals
        pool = list(range(len(employees)))
        random.shuffle(pool)
        idx = 0
        while idx < len(pool):
            group_size = random.choice([1, 2, 2, 3, 3, 4, 5])
            group = pool[idx: idx + group_size]
            t = base_time
            for g in group:
                emp_id, name, dept, pos = employees[g]
                tag_time = t + random.uniform(0, 3)
                rows.append({
                    "사번": emp_id, "이름": name, "행사명": event,
                    "태깅시간": round(tag_time, 1), "부서": dept, "직책": pos,
                })
                t += random.uniform(0.5, 3)
            base_time += random.uniform(10, 30)
            idx += group_size

    return pd.DataFrame(rows)


COLLAB_FREQUENCIES = ["주 1회 미만", "주 1회", "주 2~3회", "주 4~5회", "기타(상세사유필요)"]
ASSIGNMENT_METHODS = ["협업동료", "과제내", "부서내", "랜덤배정"]
LETTER_TYPES = ["감사", "응원", "안부", "협업", "기념일"]
ALL_KEYWORDS = ["협력", "열정", "리더십", "배려", "책임감", "도움", "존경", "친절"]


def generate_sample_peerreview(attendance_df: pd.DataFrame) -> pd.DataFrame:
    """Generate realistic peer-review sample with collab frequency and assignment method."""
    random.seed(99)
    emps = attendance_df[["사번", "부서"]].drop_duplicates().values.tolist()
    rows = []
    for emp_id, dept in emps:
        n = random.randint(4, 6)
        candidates = [(e, d) for e, d in emps if e != emp_id]
        same_dept = [(e, d) for e, d in candidates if d == dept]
        diff_dept = [(e, d) for e, d in candidates if d != dept]
        # priority: 협업동료(cross/same) > 과제내 > 부서내 > 랜덤배정
        selected: list[tuple[str, str, str]] = []
        methods_pool = (
            ["협업동료"] * 2 + ["과제내"] * 1 + ["부서내"] * 1 + ["랜덤배정"] * 2
        )
        pool = (diff_dept * 2 + same_dept * 3)
        random.shuffle(pool)
        seen: set[str] = set()
        for rev_pair in pool:
            if len(selected) >= n:
                break
            rev_id, rev_dept = rev_pair
            if rev_id in seen:
                continue
            seen.add(rev_id)
            method = methods_pool[len(selected)] if len(selected) < len(methods_pool) else "랜덤배정"
            # collaboration frequency: lower for cross-dept or low-collab (friendship bias)
            if rev_dept == dept:
                freq = random.choices(COLLAB_FREQUENCIES, weights=[0.10, 0.20, 0.35, 0.30, 0.05])[0]
            else:
                freq = random.choices(COLLAB_FREQUENCIES, weights=[0.35, 0.30, 0.20, 0.10, 0.05])[0]
            selected.append((rev_id, rev_dept, method, freq))

        for rev_id, rev_dept, method, freq in selected:
            rows.append({
                "리뷰대상자_사번": emp_id, "리뷰대상자_부서": dept,
                "리뷰어_사번": rev_id, "리뷰어_부서": rev_dept,
                "배정방식": method, "추천사유_협업빈도": freq,
            })
    return pd.DataFrame(rows)


def generate_sample_messages(attendance_df: pd.DataFrame) -> pd.DataFrame:
    """Generate realistic message-platform sample data (3 months of activity)."""
    import numpy as np
    rng = random.Random(7)
    emps = attendance_df[["사번", "부서"]].drop_duplicates().values.tolist()
    id_to_dept = {e: d for e, d in emps}
    emp_ids = [e for e, _ in emps]

    # Simulate friend clusters (employees who message each other more)
    shuffled = emp_ids[:]
    rng.shuffle(shuffled)
    cluster_size = max(3, len(shuffled) // 5)
    clusters: list[list[str]] = [shuffled[i:i+cluster_size] for i in range(0, len(shuffled), cluster_size)]

    rows = []
    from datetime import datetime, timedelta
    base_date = datetime(2025, 1, 1)

    for day_offset in range(90):  # 3 months
        cur_date = base_date + timedelta(days=day_offset)
        if cur_date.weekday() >= 5:
            continue  # weekdays only

        sent_today: dict[str, int] = {}  # sender -> daily count
        sent_this_week: dict[tuple[str, str], int] = {}  # (sender, receiver) -> weekly count

        for sender in emp_ids:
            daily = rng.choices([0, 1, 2, 3], weights=[0.50, 0.25, 0.15, 0.10])[0]
            if sent_today.get(sender, 0) >= 3:
                continue
            for _ in range(daily):
                if sent_today.get(sender, 0) >= 3:
                    break
                # find receiver — bias toward same cluster
                sender_cluster = next((c for c in clusters if sender in c), [])
                if sender_cluster and rng.random() < 0.55:
                    candidates = [e for e in sender_cluster if e != sender]
                else:
                    candidates = [e for e in emp_ids if e != sender]
                # weekly 1-message limit to same person
                candidates = [e for e in candidates if sent_this_week.get((sender, e), 0) < 1]
                if not candidates:
                    continue
                receiver = rng.choice(candidates)

                hour = rng.randint(9, 18)
                minute = rng.randint(0, 59)
                dt = cur_date + timedelta(hours=hour, minutes=minute)

                letter = rng.choice(LETTER_TYPES)
                kw_count = rng.randint(1, 3)
                kws = rng.sample(ALL_KEYWORDS, kw_count)
                is_anon = rng.random() < 0.12

                rows.append({
                    "발신자_아이디": sender,
                    "실제발신자_아이디": sender,
                    "수신자_아이디": receiver,
                    "발송일시": dt,
                    "레터유형": letter,
                    "키워드": ",".join(kws),
                    "익명여부": "Y" if is_anon else "N",
                    "발신자_부서": id_to_dept.get(sender, "미분류"),
                    "수신자_부서": id_to_dept.get(receiver, "미분류"),
                })
                sent_today[sender] = sent_today.get(sender, 0) + 1
                sent_this_week[(sender, receiver)] = sent_this_week.get((sender, receiver), 0) + 1

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _parse_time(raw) -> Optional[float]:
    """Convert various time representations to float seconds."""
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(raw)
    except (ValueError, TypeError):
        pass
    try:
        import pandas as pd
        ts = pd.to_datetime(raw)
        return ts.timestamp()
    except Exception:
        return None


def _assign_colors(keys) -> dict:
    palette = [
        "#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6",
        "#1abc9c", "#e67e22", "#34495e", "#e91e63", "#00bcd4",
        "#8bc34a", "#ff5722", "#607d8b", "#795548", "#ffeb3b",
    ]
    return {k: palette[i % len(palette)] for i, k in enumerate(keys)}
