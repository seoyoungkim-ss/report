"""
Confluence REST API 기반 임직원 지식활동 추출 모듈
- Atlassian Cloud (api_token) 및 Server (username/password) 지원
- 샘플 데이터 생성기 내장 (API 연결 없이도 분석 가능)
"""
from __future__ import annotations

import re
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

import pandas as pd


# ────────────────────────────────────────────────────────────────────
# Domain constants
# ────────────────────────────────────────────────────────────────────

SPACE_KEYS = ["HR", "DEV", "MKT", "FIN", "PLAN"]
SPACE_NAMES = {"HR": "인사·조직", "DEV": "개발·기술", "MKT": "마케팅", "FIN": "재무·회계", "PLAN": "경영기획"}
DEPT_TO_SPACE = {
    "인사팀": "HR", "개발팀": "DEV", "마케팅팀": "MKT",
    "재무팀": "FIN", "기획팀": "PLAN",
}
PAGE_TOPICS = {
    "HR":   ["온보딩", "채용공고", "조직문화", "복리후생", "성과평가", "교육훈련", "인사규정", "HR 데이터"],
    "DEV":  ["시스템 아키텍처", "API 문서", "코드 리뷰 가이드", "배포 절차", "기술부채", "보안점검", "QA 체크리스트"],
    "MKT":  ["캠페인 기획", "브랜드 가이드", "SNS 운영", "고객 인사이트", "시장조사", "콘텐츠 전략"],
    "FIN":  ["예산 계획", "결산 보고", "비용 정책", "투자 검토", "감사 대응", "세무 처리"],
    "PLAN": ["중장기 전략", "KPI 현황", "OKR 설정", "경쟁사 분석", "사업 계획서", "임원 보고"],
}
ALL_LABELS = ["중요", "공유", "검토필요", "완료", "진행중", "아카이브", "FAQ", "템플릿", "보고서", "가이드"]


# ────────────────────────────────────────────────────────────────────
# Data classes
# ────────────────────────────────────────────────────────────────────

@dataclass
class ConfluencePage:
    page_id: str
    title: str
    space_key: str
    space_name: str
    author_id: str          # Confluence accountId or username → mapped to 사번
    created_at: datetime
    updated_at: datetime
    contributors: list[str] = field(default_factory=list)   # list of user ids
    mentions: list[str]     = field(default_factory=list)   # @mentioned user ids
    labels: list[str]       = field(default_factory=list)
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    word_count: int = 0
    content_snippet: str = ""


@dataclass
class ConfluenceComment:
    comment_id: str
    page_id: str
    author_id: str
    created_at: datetime
    content_snippet: str = ""


@dataclass
class ConfluenceSpace:
    key: str
    name: str
    description: str = ""
    page_count: int = 0


# ────────────────────────────────────────────────────────────────────
# Confluence REST API Client
# ────────────────────────────────────────────────────────────────────

class ConfluenceClient:
    """
    Confluence REST API v1 클라이언트.
    Cloud: email + API token
    Server: username + password
    """

    def __init__(self, base_url: str, email: str, token: str, is_cloud: bool = True):
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/wiki/rest/api"
        self.email = email
        self.token = token
        self.is_cloud = is_cloud
        self._session = None

    def _get_session(self):
        try:
            import requests
            from requests.auth import HTTPBasicAuth
        except ImportError:
            raise ImportError("requests 패키지가 필요합니다: pip install requests")

        if self._session is None:
            import requests
            from requests.auth import HTTPBasicAuth
            self._session = requests.Session()
            self._session.auth = HTTPBasicAuth(self.email, self.token)
            self._session.headers.update({
                "Accept": "application/json",
                "Content-Type": "application/json",
            })
        return self._session

    def test_connection(self) -> tuple[bool, str]:
        try:
            s = self._get_session()
            r = s.get(f"{self.api_base}/user/current", timeout=10)
            if r.status_code == 200:
                user = r.json()
                name = user.get("displayName", user.get("username", "unknown"))
                return True, f"연결 성공 — {name}"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, str(e)

    def fetch_spaces(self) -> list[dict]:
        s = self._get_session()
        spaces, start = [], 0
        while True:
            r = s.get(f"{self.api_base}/space", params={"limit": 50, "start": start})
            r.raise_for_status()
            data = r.json()
            spaces.extend(data.get("results", []))
            if data.get("size", 0) < 50:
                break
            start += 50
        return spaces

    def fetch_pages(self, space_key: str, cql_extra: str = "") -> list[dict]:
        s = self._get_session()
        cql = f'space = "{space_key}" AND type = "page"'
        if cql_extra:
            cql += f" AND ({cql_extra})"
        pages, start = [], 0
        while True:
            r = s.get(f"{self.api_base}/content/search", params={
                "cql": cql, "limit": 50, "start": start,
                "expand": "history,metadata.labels,version,space",
            })
            r.raise_for_status()
            data = r.json()
            pages.extend(data.get("results", []))
            if data.get("size", 0) < 50:
                break
            start += 50
        return pages

    def fetch_page_detail(self, page_id: str) -> dict:
        s = self._get_session()
        r = s.get(f"{self.api_base}/content/{page_id}", params={
            "expand": "body.storage,history,version,metadata.labels,metadata.properties",
        })
        r.raise_for_status()
        return r.json()

    def fetch_page_history(self, page_id: str) -> list[dict]:
        """편집 이력 — 기여자 추출에 사용"""
        s = self._get_session()
        r = s.get(f"{self.api_base}/content/{page_id}/history/next/versions", timeout=15)
        if r.status_code == 200:
            return r.json().get("results", [])
        return []

    def fetch_page_comments(self, page_id: str) -> list[dict]:
        s = self._get_session()
        r = s.get(f"{self.api_base}/content/{page_id}/child/comment", params={
            "expand": "body.view,version,history", "limit": 200,
        })
        if r.status_code == 200:
            return r.json().get("results", [])
        return []

    def fetch_all_pages_from_spaces(
        self,
        space_keys: list[str],
        cql_filter: str = "",
        progress_cb=None,
    ) -> tuple[list[ConfluencePage], list[ConfluenceComment]]:
        """여러 스페이스의 모든 페이지·댓글을 가져와 파싱합니다."""
        all_pages: list[ConfluencePage] = []
        all_comments: list[ConfluenceComment] = []
        for i, sk in enumerate(space_keys):
            if progress_cb:
                progress_cb(i / len(space_keys), f"스페이스 {sk} 처리 중…")
            raw_pages = self.fetch_pages(sk, cql_filter)
            for rp in raw_pages:
                page = _parse_api_page(rp)
                # detailed body for mention extraction
                try:
                    detail = self.fetch_page_detail(rp["id"])
                    body_html = (detail.get("body", {})
                                       .get("storage", {})
                                       .get("value", ""))
                    page.mentions = extract_user_mentions(body_html)
                    page.word_count = len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", body_html)))
                except Exception:
                    pass
                all_pages.append(page)

                # Comments
                try:
                    for c in self.fetch_page_comments(rp["id"]):
                        cm = _parse_api_comment(c, rp["id"])
                        if cm:
                            all_comments.append(cm)
                except Exception:
                    pass
        return all_pages, all_comments


# ────────────────────────────────────────────────────────────────────
# API response parsers
# ────────────────────────────────────────────────────────────────────

def _parse_api_page(raw: dict) -> ConfluencePage:
    history = raw.get("history", {})
    created_by = history.get("createdBy", {})
    author_id = created_by.get("accountId") or created_by.get("username") or "unknown"

    created_str = history.get("createdDate", "")
    try:
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    except Exception:
        created_at = datetime.now()

    version = raw.get("version", {})
    updated_str = version.get("when", "")
    try:
        updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
    except Exception:
        updated_at = created_at

    labels = [lb["name"] for lb in raw.get("metadata", {})
              .get("labels", {}).get("results", [])]

    space = raw.get("space", {})
    return ConfluencePage(
        page_id=raw.get("id", ""),
        title=raw.get("title", ""),
        space_key=space.get("key", ""),
        space_name=space.get("name", ""),
        author_id=author_id,
        created_at=created_at,
        updated_at=updated_at,
        labels=labels,
        view_count=raw.get("extensions", {}).get("views", 0),
    )


def _parse_api_comment(raw: dict, page_id: str) -> Optional[ConfluenceComment]:
    history = raw.get("history", {})
    created_by = history.get("createdBy", {})
    author_id = created_by.get("accountId") or created_by.get("username") or ""
    if not author_id:
        return None
    created_str = history.get("createdDate", "")
    try:
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    except Exception:
        created_at = datetime.now()
    snippet = (raw.get("body", {}).get("view", {}).get("value", "")[:120]
               .replace("\n", " ").strip())
    return ConfluenceComment(
        comment_id=raw.get("id", ""),
        page_id=page_id,
        author_id=author_id,
        created_at=created_at,
        content_snippet=snippet,
    )


# ────────────────────────────────────────────────────────────────────
# HTML / Storage-format parsing utilities
# ────────────────────────────────────────────────────────────────────

_MENTION_PATTERNS = [
    re.compile(r'ri:account-id="([^"]+)"'),      # Cloud storage format
    re.compile(r'ri:username="([^"]+)"'),          # Server storage format
    re.compile(r'data-username="([^"]+)"'),         # Atlassian mention macro
    re.compile(r'@([A-Za-z0-9_\-\.]{3,40})'),     # plain @mention
]

def extract_user_mentions(html_content: str) -> list[str]:
    """Confluence 페이지 HTML에서 @멘션된 사용자 ID 목록을 추출합니다."""
    found: set[str] = set()
    for pat in _MENTION_PATTERNS:
        for m in pat.findall(html_content):
            if m:
                found.add(m.strip())
    return list(found)


# ────────────────────────────────────────────────────────────────────
# Sample Data Generator
# ────────────────────────────────────────────────────────────────────

def generate_sample_confluence_data(
    emp_df: pd.DataFrame,
    n_pages: int = 120,
    n_months: int = 6,
    seed: int = 42,
) -> tuple[list[ConfluencePage], list[ConfluenceComment], dict[str, str]]:
    """
    샘플 Confluence 데이터 생성.
    Returns (pages, comments, confluence_id_to_emp_id_map)
    emp_df: columns ['사번', '부서'] (from generate_sample_attendance)
    """
    rng = random.Random(seed)
    emps = emp_df[["사번", "부서"]].drop_duplicates().values.tolist()
    emp_ids = [e for e, _ in emps]
    id_to_dept = {e: d for e, d in emps}

    # Confluence user IDs = "conf_" + 사번 (sample mapping)
    cf_id_map: dict[str, str] = {f"conf_{e}": e for e in emp_ids}
    cf_ids = list(cf_id_map.keys())  # confluence IDs

    # Determine active authors per space (dept-based bias)
    dept_cf_ids: dict[str, list[str]] = defaultdict(list)
    for cf, emp in cf_id_map.items():
        dept = id_to_dept[emp]
        space = DEPT_TO_SPACE.get(dept, rng.choice(SPACE_KEYS))
        dept_cf_ids[space].append(cf)

    base_dt = datetime(2025, 1, 1)
    pages: list[ConfluencePage] = []
    comments: list[ConfluenceComment] = []
    page_counter, comment_counter = 0, 0

    for _ in range(n_pages):
        # Pick space — bias toward dept's space but allow cross-space
        space_key = rng.choices(
            SPACE_KEYS,
            weights=[0.25, 0.25, 0.20, 0.15, 0.15],
        )[0]
        home_authors = dept_cf_ids.get(space_key, cf_ids)

        # Author — mostly home dept, sometimes cross-dept
        if home_authors and rng.random() < 0.75:
            author = rng.choice(home_authors)
        else:
            author = rng.choice(cf_ids)

        # Dates
        days_offset = rng.randint(0, n_months * 30)
        created_at = base_dt + timedelta(days=days_offset, hours=rng.randint(8, 18))
        updated_at = created_at + timedelta(days=rng.randint(0, 30), hours=rng.randint(0, 5))

        # Title
        topic = rng.choice(PAGE_TOPICS.get(space_key, ["일반 문서"]))
        title = f"{topic} — {created_at.strftime('%Y.%m')}"

        # Contributors (besides author)
        n_contrib = rng.randint(0, 4)
        pool = [c for c in cf_ids if c != author]
        # Bias: same-space contributors more likely
        same_space = [c for c in home_authors if c != author]
        contrib_pool = same_space * 3 + pool
        contributors = list(set(rng.sample(contrib_pool, min(n_contrib, len(contrib_pool)))))

        # Mentions — random subset of contributors + possibly others
        mention_pool = contributors + rng.sample(pool, min(2, len(pool)))
        n_mentions = rng.randint(0, min(4, len(mention_pool)))
        mentions = list(set(rng.sample(mention_pool, n_mentions)))

        # Labels
        n_labels = rng.randint(0, 3)
        labels = rng.sample(ALL_LABELS, n_labels)

        # Metrics
        view_count    = rng.randint(10, 500)
        like_count    = rng.randint(0, view_count // 8)
        comment_count = rng.randint(0, 8)
        word_count    = rng.randint(150, 2000)

        page_id = f"P{page_counter:04d}"
        pages.append(ConfluencePage(
            page_id=page_id,
            title=title,
            space_key=space_key,
            space_name=SPACE_NAMES[space_key],
            author_id=author,
            created_at=created_at,
            updated_at=updated_at,
            contributors=contributors,
            mentions=mentions,
            labels=labels,
            view_count=view_count,
            like_count=like_count,
            comment_count=comment_count,
            word_count=word_count,
            content_snippet=f"[{topic}] 관련 문서입니다.",
        ))
        page_counter += 1

        # Generate comments
        for _ in range(comment_count):
            commenter = rng.choice([c for c in cf_ids if c != author])
            c_dt = created_at + timedelta(days=rng.randint(0, 14), hours=rng.randint(0, 8))
            comments.append(ConfluenceComment(
                comment_id=f"C{comment_counter:05d}",
                page_id=page_id,
                author_id=commenter,
                created_at=c_dt,
                content_snippet=rng.choice([
                    "내용 잘 읽었습니다. 추가로 검토가 필요한 부분이 있어 코멘트 남깁니다.",
                    "좋은 문서 감사합니다. 몇 가지 의견 공유드립니다.",
                    "업데이트 감사합니다. 확인했습니다.",
                    "리뷰 완료했습니다. 승인드립니다.",
                    "추가 설명 부탁드립니다.",
                ])
            ))
            comment_counter += 1

    return pages, comments, cf_id_map


# ────────────────────────────────────────────────────────────────────
# Analysis helpers — DataFrame builders
# ────────────────────────────────────────────────────────────────────

def build_page_df(pages: list[ConfluencePage]) -> pd.DataFrame:
    return pd.DataFrame([{
        "page_id":       p.page_id,
        "제목":           p.title,
        "스페이스":        p.space_name,
        "space_key":     p.space_key,
        "작성자ID":        p.author_id,
        "생성일":          p.created_at,
        "수정일":          p.updated_at,
        "레이블":          ",".join(p.labels),
        "조회수":          p.view_count,
        "좋아요":          p.like_count,
        "댓글수":          p.comment_count,
        "단어수":          p.word_count,
        "기여자수":        len(p.contributors),
        "멘션수":          len(p.mentions),
    } for p in pages])


def build_user_activity_df(
    pages: list[ConfluencePage],
    comments: list[ConfluenceComment],
    cf_to_emp: dict[str, str],    # confluence_id -> 사번
) -> pd.DataFrame:
    """사용자별 Confluence 활동 집계 DataFrame."""
    all_cf_ids = set(cf_to_emp.keys())
    # Also include IDs that appear in pages/comments but aren't in mapping
    for p in pages:
        all_cf_ids.add(p.author_id)
        all_cf_ids.update(p.contributors)
        all_cf_ids.update(p.mentions)
    for c in comments:
        all_cf_ids.add(c.author_id)

    stats: dict[str, dict] = {cid: {
        "authored_pages": 0,
        "contributed_pages": 0,
        "mentioned_in": 0,
        "mentioned_others": 0,
        "comments_written": 0,
        "comments_received": 0,
        "total_views": 0,
        "total_likes": 0,
        "total_words": 0,
        "spaces_active": set(),
        "labels_used": [],
        "topics": [],
        "cross_space_contrib": 0,
    } for cid in all_cf_ids}

    for p in pages:
        a = p.author_id
        if a in stats:
            stats[a]["authored_pages"] += 1
            stats[a]["total_views"]    += p.view_count
            stats[a]["total_likes"]    += p.like_count
            stats[a]["total_words"]    += p.word_count
            stats[a]["spaces_active"].add(p.space_key)
            stats[a]["labels_used"].extend(p.labels)
            stats[a]["topics"].append(p.title)

        for cid in p.contributors:
            if cid in stats:
                stats[cid]["contributed_pages"] += 1
                stats[cid]["spaces_active"].add(p.space_key)
                # cross-space: contributed to a space not their "home"
                # We approximate: if they authored more in another space
                stats[cid]["cross_space_contrib"] += 1

        for cid in p.mentions:
            if cid in stats:
                stats[cid]["mentioned_in"] += 1
            if a in stats:
                stats[a]["mentioned_others"] += 1

    for cm in comments:
        cid = cm.author_id
        if cid in stats:
            stats[cid]["comments_written"] += 1
        # find page author to credit "comments_received"
        page_authors = {p.page_id: p.author_id for p in pages}
        pa = page_authors.get(cm.page_id)
        if pa and pa in stats:
            stats[pa]["comments_received"] += 1

    rows = []
    for cid, s in stats.items():
        emp_id = cf_to_emp.get(cid, cid)
        rows.append({
            "confluence_id":   cid,
            "사번":             emp_id,
            "작성 페이지":       s["authored_pages"],
            "기여 페이지":       s["contributed_pages"],
            "멘션 수신":         s["mentioned_in"],
            "멘션 발신":         s["mentioned_others"],
            "댓글 작성":         s["comments_written"],
            "댓글 수신":         s["comments_received"],
            "누적 조회수":       s["total_views"],
            "누적 좋아요":       s["total_likes"],
            "총 작성 단어수":    s["total_words"],
            "활동 스페이스 수":  len(s["spaces_active"]),
            "교차 스페이스 기여":s["cross_space_contrib"],
            "총활동":            (s["authored_pages"] * 3
                                  + s["contributed_pages"] * 2
                                  + s["comments_written"]
                                  + s["mentioned_in"]),
        })

    return pd.DataFrame(rows).sort_values("총활동", ascending=False).reset_index(drop=True)


def build_mention_network_df(
    pages: list[ConfluencePage],
    cf_to_emp: dict[str, str],
) -> pd.DataFrame:
    """멘션 기반 방향 엣지 DataFrame (author → mentioned_user)."""
    rows = []
    for p in pages:
        for m in p.mentions:
            if m != p.author_id:
                rows.append({
                    "from_cf": p.author_id,
                    "to_cf":   m,
                    "from_emp": cf_to_emp.get(p.author_id, p.author_id),
                    "to_emp":   cf_to_emp.get(m, m),
                    "space":   p.space_key,
                    "page_title": p.title,
                })
    return pd.DataFrame(rows)


def build_coauthor_network_df(
    pages: list[ConfluencePage],
    cf_to_emp: dict[str, str],
) -> pd.DataFrame:
    """공동 기여 기반 무방향 엣지 DataFrame."""
    from itertools import combinations
    rows = []
    for p in pages:
        all_authors = list(set([p.author_id] + p.contributors))
        for a, b in combinations(all_authors, 2):
            rows.append({
                "emp_a": cf_to_emp.get(a, a),
                "emp_b": cf_to_emp.get(b, b),
                "space": p.space_key,
                "page_title": p.title,
            })
    if not rows:
        return pd.DataFrame(columns=["emp_a","emp_b","space","page_title"])
    df = pd.DataFrame(rows)
    # Deduplicate by pair
    df["pair"] = df.apply(lambda r: tuple(sorted([r["emp_a"], r["emp_b"]])), axis=1)
    counts = df.groupby("pair").size().reset_index(name="공동기여 횟수")
    counts[["emp_a","emp_b"]] = pd.DataFrame(counts["pair"].tolist(), index=counts.index)
    return counts[["emp_a","emp_b","공동기여 횟수"]]


def compute_knowledge_score(activity_df: pd.DataFrame) -> pd.DataFrame:
    """
    Confluence 지식기여 점수 정규화 (0~100).
    authored_pages, views, cross_space, mentions_received 기반.
    """
    df = activity_df.copy()
    cols = ["작성 페이지", "누적 조회수", "교차 스페이스 기여", "멘션 수신", "댓글 작성"]
    for c in cols:
        if c in df.columns:
            mx = df[c].max()
            df[f"_norm_{c}"] = df[c] / mx if mx > 0 else 0.0

    weights = {"작성 페이지": 0.35, "누적 조회수": 0.25,
               "교차 스페이스 기여": 0.20, "멘션 수신": 0.10, "댓글 작성": 0.10}
    df["confluence_score"] = sum(
        df.get(f"_norm_{c}", 0) * w for c, w in weights.items()
    ) * 100
    return df.drop(columns=[c for c in df.columns if c.startswith("_norm_")])
