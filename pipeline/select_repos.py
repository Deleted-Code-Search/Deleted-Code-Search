"""저장소 선정 스크립트 — CHARTER.md §4.3 기준으로 후보를 산출한다. 담당: 성제 (sj)

무엇을:
    GitHub Search API로 Python 주 언어 저장소를 모아 §4.3 기준으로 거르고, 최근 1년 커밋
    표본에서 "PR 경유 비율"과 "커밋·PR의 #번호 이슈 참조 비율"을 재서 점수순 CSV로 쓴다.
    §4.1 흐름도의 맨 위 [저장소 선정 목록]을 만드는 단계라 pipeline/ 에 둔다.

왜 별 수로 정렬하지 않나 (ADR-006):
    별 수와 이유 회수율은 무관하다. 점수는 PR 문화(0.5)·이슈 참조(0.3)·규모(0.2)로만 낸다.
    stars 는 참고용으로 CSV에 남기되 점수 계산에는 넣지 않는다.

왜 permissive 라이선스만인가 (ADR-007):
    데이터셋에 코드 스니펫을 재배포하므로 MIT / Apache-2.0 / BSD 계열만 후보로 둔다.

실행:
    python -m pipeline.select_repos --out docs/repo_candidates.csv

    GITHUB_TOKEN 은 .env 에서만 읽는다 (§8.4). API 응답은 캐시 디렉터리에 저장하고
    재실행 때는 캐시를 먼저 본다 (§7 한도 5,000회/시간).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "deleted-code-search-repo-selector"

# §4.3 ②, ADR-007. GitHub 는 spdx_id 를 "MIT", "Apache-2.0" 처럼 준다.
PERMISSIVE_SPDX = frozenset(
    {
        "mit",
        "mit-0",
        "apache-2.0",
        "bsd-2-clause",
        "bsd-3-clause",
        "bsd-3-clause-clear",
        "bsd-4-clause",
        "0bsd",
    }
)

MIN_COMMITS = 1000  # §4.3 ⑤
PR_RATIO_GATE = 0.70  # §4.3 ③
DEFAULT_SINCE_DAYS = 365  # §4.3 ③ "최근 1년"

# 점수 가중치. 합이 1.0 이 되게 유지한다 (바꾸면 docs 의 선정 근거도 같이 고친다).
W_PR_RATIO = 0.5
W_ISSUE_REF = 0.3
W_SCALE = 0.2
SCALE_SATURATION_COMMITS = 50_000  # 이 이상은 규모 점수 1.0 으로 포화

# §4.3 "초기 후보 예시". 검색에 안 잡혀도 항상 평가해 포함/제외 이유를 CSV 에 남긴다.
CHARTER_SEED_REPOS = (
    "django/django",
    "psf/requests",
    "pallets/flask",
    "fastapi/fastapi",
    "encode/httpx",
    "pydantic/pydantic",
    "pandas-dev/pandas",
    "scikit-learn/scikit-learn",
    "celery/celery",
    "sqlalchemy/sqlalchemy",
)

CSV_COLUMNS = (
    "repo",
    "license",
    "commits",
    "pr_ratio",
    "issue_ref_ratio",
    "stars",
    "score",
    "default_branch",
    "size_kb",
    "sampled_commits",
    "pr_gate_pass",
    "selection_status",
    "exclude_reason",
)

ISSUE_REF_RE = re.compile(r"#(\d+)")
# 머지 커밋 제목 ("Merge pull request #123 from ..."), 스쿼시 머지 제목 접미사 ("... (#123)")
MERGE_TITLE_RE = re.compile(r"Merge (?:pull request|PR) #(\d+)", re.IGNORECASE)
SQUASH_SUFFIX_RE = re.compile(r"\(#(\d+)\)\s*$")
LINK_LAST_PAGE_RE = re.compile(r"[?&]page=(\d+)>;\s*rel=\"last\"")


# --------------------------------------------------------------------------------------
# .env (§8.4 — 토큰은 .env 에만)
# --------------------------------------------------------------------------------------


def parse_dotenv(text: str) -> dict[str, str]:
    """.env 본문을 파싱한다. 주석·빈 줄·export 접두사·따옴표를 처리한다."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_env_file(path: Path) -> dict[str, str]:
    """.env 를 읽어 os.environ 에 없는 값만 채운다. 파일이 없으면 조용히 넘어간다."""
    if not path.is_file():
        return {}
    values = parse_dotenv(path.read_text(encoding="utf-8"))
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


# --------------------------------------------------------------------------------------
# GitHub API 클라이언트 (캐시 + 레이트리밋)
# --------------------------------------------------------------------------------------


@dataclass
class Response:
    """캐시에 그대로 담기는 최소 응답."""

    status: int
    headers: dict[str, str]
    body: Any


Fetcher = Callable[[str, dict[str, str]], tuple[int, dict[str, str], bytes]]


def _urlopen_fetch(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        lowered = {key.lower(): value for key, value in response.headers.items()}
        return response.status, lowered, response.read()


class GitHubClient:
    """GET 전용 GitHub 클라이언트. 성공 응답을 디스크에 캐시한다 (재실행 시 재호출 없음)."""

    def __init__(
        self,
        token: str,
        cache_dir: Path,
        *,
        refresh: bool = False,
        max_wait_seconds: int = 900,
        fetcher: Fetcher | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.token = token
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.max_wait_seconds = max_wait_seconds
        self.fetcher = fetcher or _urlopen_fetch
        self.sleep = sleep
        self.log = log or (lambda message: None)
        self.api_calls = 0
        self.cache_hits = 0
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- 캐시 --------------------------------------------------------------------------

    def cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> Response | None:
        path = self.cache_path(url)
        if self.refresh or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return Response(payload["status"], payload["headers"], payload["body"])

    def _write_cache(self, url: str, response: Response) -> None:
        payload = {
            "url": url,
            "status": response.status,
            "headers": response.headers,
            "body": response.body,
            "fetched_at": datetime.now(UTC).isoformat(),
        }
        self.cache_path(url).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # -- 요청 --------------------------------------------------------------------------

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _wait_for_reset(self, headers: dict[str, str]) -> bool:
        """레이트리밋이 바닥나면 리셋까지 기다린다. 기다릴 수 있었으면 True."""
        retry_after = headers.get("retry-after", "")
        if retry_after.isdigit():
            wait = int(retry_after)
        else:
            reset = headers.get("x-ratelimit-reset", "")
            wait = int(int(reset) - time.time()) + 5 if reset.isdigit() else 60
        wait = max(1, wait)
        if wait > self.max_wait_seconds:
            return False
        self.log(f"레이트리밋 대기 {wait}초 (§7 한도 5,000회/시간)")
        self.sleep(wait)
        return True

    def get(
        self, path: str, params: dict[str, Any] | None = None, *, allow_404: bool = False
    ) -> Response | None:
        url = API_ROOT + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        cached = self._read_cache(url)
        if cached is not None:
            self.cache_hits += 1
            if cached.status == 404:
                if allow_404:
                    return None
                raise RuntimeError(f"404 (캐시): {url}")
            return cached

        for attempt in range(4):
            try:
                self.api_calls += 1
                status, headers, raw = self.fetcher(url, self._request_headers())
            except urllib.error.HTTPError as error:
                error_headers = (
                    {key.lower(): value for key, value in error.headers.items()}
                    if error.headers
                    else {}
                )
                if error.code == 404:
                    self._write_cache(url, Response(404, error_headers, None))
                    if allow_404:
                        return None
                    raise RuntimeError(f"404: {url}") from error
                if error.code in (403, 429) and attempt < 3 and self._wait_for_reset(error_headers):
                    continue
                raise RuntimeError(f"HTTP {error.code}: {url}") from error
            except urllib.error.URLError as error:
                if attempt < 3:
                    self.sleep(2**attempt)
                    continue
                raise RuntimeError(f"네트워크 오류: {url}") from error

            body = json.loads(raw.decode("utf-8")) if raw else None
            response = Response(status, headers, body)
            self._write_cache(url, response)
            remaining = headers.get("x-ratelimit-remaining", "")
            if remaining.isdigit() and int(remaining) <= 1:
                self._wait_for_reset(headers)
            return response

        raise RuntimeError(f"요청 실패: {url}")

    def get_json(
        self, path: str, params: dict[str, Any] | None = None, *, allow_404: bool = False
    ) -> Any:
        response = self.get(path, params, allow_404=allow_404)
        return None if response is None else response.body

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_items: int,
        max_pages: int = 10,
    ) -> list[Any]:
        items: list[Any] = []
        page = 1
        while len(items) < max_items and page <= max_pages:
            per_page = min(100, max_items)
            page_params = dict(params or {})
            page_params.update({"per_page": per_page, "page": page})
            body = self.get_json(path, page_params)
            if not body:
                break
            items.extend(body)
            if len(body) < per_page:
                break
            page += 1
        return items[:max_items]


# --------------------------------------------------------------------------------------
# 순수 계산 (테스트 대상)
# --------------------------------------------------------------------------------------


def is_permissive_license(spdx_id: str | None) -> bool:
    """MIT / Apache-2.0 / BSD 계열이면 True (§4.3 ②, ADR-007)."""
    if not spdx_id:
        return False
    return spdx_id.strip().lower() in PERMISSIVE_SPDX


def commit_count_from_link(link_header: str | None, page_item_count: int) -> int:
    """per_page=1 응답의 Link 헤더에서 총 커밋 수를 읽는다.

    Link 가 없으면 커밋이 한 페이지뿐이라는 뜻이므로 받은 개수를 그대로 쓴다.
    """
    if link_header:
        match = LINK_LAST_PAGE_RE.search(link_header)
        if match:
            return int(match.group(1))
    return page_item_count


def strip_pr_markers(message: str) -> str:
    """머지·스쿼시 머지가 남긴 PR 번호 표시를 지운다. 이슈 참조와 구분하기 위함."""
    lines = message.splitlines() or [""]
    lines[0] = SQUASH_SUFFIX_RE.sub("", MERGE_TITLE_RE.sub("", lines[0])).rstrip()
    return "\n".join(lines)


def extract_issue_refs(text: str | None, exclude: Sequence[int] = ()) -> tuple[int, ...]:
    """본문에서 #번호 이슈 참조를 뽑는다. PR 자기 번호는 제외한다 (§4.3 ④)."""
    if not text:
        return ()
    numbers = {int(number) for number in ISSUE_REF_RE.findall(strip_pr_markers(text))}
    return tuple(sorted(numbers - set(exclude)))


@dataclass
class CommitSample:
    """커밋 한 건의 판정 결과."""

    sha: str
    via_pr: bool
    issue_refs: tuple[int, ...]

    @property
    def refs_issue(self) -> bool:
        return bool(self.issue_refs)


def summarize_commit(sha: str, message: str, pulls: Sequence[dict[str, Any]]) -> CommitSample:
    """커밋 + 연결된 PR 목록으로 "PR 경유 여부"와 "이슈 참조 여부"를 정한다.

    - via_pr: 이 커밋을 담은 **머지된** PR 이 하나라도 있으면 True.
      열려만 있는 PR 은 아직 main 에 들어온 경로가 아니므로 세지 않는다.
    - issue_refs: 커밋 메시지 + 머지된 PR 의 제목·본문에서 모은 #번호.
      PR 자기 번호("Merge pull request #123", "... (#123)")는 이슈 참조로 세지 않는다.
    """
    merged = [pull for pull in pulls if pull.get("merged_at")]
    own_numbers = [pull["number"] for pull in pulls if pull.get("number") is not None]

    refs = set(extract_issue_refs(message, own_numbers))
    for pull in merged:
        refs.update(extract_issue_refs(pull.get("title"), own_numbers))
        refs.update(extract_issue_refs(pull.get("body"), own_numbers))

    return CommitSample(sha=sha, via_pr=bool(merged), issue_refs=tuple(sorted(refs)))


def compute_ratios(samples: Sequence[CommitSample]) -> tuple[float, float]:
    """(PR 경유 비율, 이슈 참조 비율). 표본이 없으면 (0.0, 0.0)."""
    if not samples:
        return 0.0, 0.0
    total = len(samples)
    pr_ratio = sum(1 for sample in samples if sample.via_pr) / total
    issue_ratio = sum(1 for sample in samples if sample.refs_issue) / total
    return pr_ratio, issue_ratio


def scale_score(commits: int) -> float:
    """규모 점수. 1,000 커밋에서 0, 50,000 커밋에서 1.0 으로 포화하는 로그 스케일."""
    if commits <= MIN_COMMITS:
        return 0.0
    ratio = math.log10(commits / MIN_COMMITS) / math.log10(SCALE_SATURATION_COMMITS / MIN_COMMITS)
    return min(1.0, ratio)


def score_candidate(pr_ratio: float, issue_ref_ratio: float, commits: int) -> float:
    """선정 점수 (0.0 ~ 1.0). 별 수는 의도적으로 넣지 않는다 (ADR-006)."""
    weighted = (
        W_PR_RATIO * pr_ratio + W_ISSUE_REF * issue_ref_ratio + W_SCALE * scale_score(commits)
    )
    return round(weighted, 4)


# --------------------------------------------------------------------------------------
# 저장소 평가
# --------------------------------------------------------------------------------------


@dataclass
class RepoRow:
    """CSV 한 줄. 제외된 저장소도 이유와 함께 남긴다 (킥오프에서 근거로 쓴다)."""

    repo: str
    license_id: str = ""
    commits: int | None = None
    pr_ratio: float | None = None
    issue_ref_ratio: float | None = None
    stars: int | None = None
    score: float | None = None
    default_branch: str = ""
    size_kb: int | None = None
    sampled_commits: int = 0
    exclude_reason: str = ""

    @property
    def selected(self) -> bool:
        return not self.exclude_reason

    @property
    def pr_gate_pass(self) -> bool:
        return self.pr_ratio is not None and self.pr_ratio >= PR_RATIO_GATE

    def to_csv_row(self) -> dict[str, Any]:
        def num(value: float | None, digits: int) -> str:
            return "" if value is None else f"{value:.{digits}f}"

        return {
            "repo": self.repo,
            "license": self.license_id,
            "commits": "" if self.commits is None else self.commits,
            "pr_ratio": num(self.pr_ratio, 3),
            "issue_ref_ratio": num(self.issue_ref_ratio, 3),
            "stars": "" if self.stars is None else self.stars,
            "score": num(self.score, 4),
            "default_branch": self.default_branch,
            "size_kb": "" if self.size_kb is None else self.size_kb,
            "sampled_commits": self.sampled_commits,
            "pr_gate_pass": "true" if self.pr_gate_pass else "false",
            "selection_status": "CANDIDATE" if self.selected else "EXCLUDED",
            "exclude_reason": self.exclude_reason,
        }


def _row_from_repo_item(full_name: str, item: dict[str, Any]) -> RepoRow:
    license_info = item.get("license") or {}
    return RepoRow(
        repo=item.get("full_name") or full_name,
        license_id=license_info.get("spdx_id") or "",
        stars=item.get("stargazers_count"),
        default_branch=item.get("default_branch") or "",
        size_kb=item.get("size"),
    )


def since_iso(days: int, now: datetime | None = None) -> str:
    """`--since-days` 만큼 거슬러 올라간 UTC 시각.

    자정으로 잘라 쓴다. 시:분:초까지 넣으면 실행할 때마다 커밋 목록 URL 이 달라져
    캐시가 매번 빗나간다 (같은 날 재실행은 호출 0회여야 한다).
    """
    moment = (now or datetime.now(UTC)) - timedelta(days=days)
    return moment.strftime("%Y-%m-%dT00:00:00Z")


def is_bot_commit(commit: dict[str, Any]) -> bool:
    """봇이 만든 커밋인가.

    릴리스 노트 갱신·의존성 범프 봇은 PR 없이 main 에 직접 밀어 넣는 경우가 많아,
    표본에 들어가면 PR 경유 비율을 실제보다 낮게 만든다 (fastapi 표본 30건 중 28건이 봇이었다).
    삭제 코드 채굴 대상도 사람이 쓴 커밋이므로 여기서 뺀다.
    """
    author = commit.get("author") or {}
    if author.get("type") == "Bot":
        return True
    login = author.get("login") or ""
    return login.endswith("[bot]")


def collect_commit_samples(
    client: GitHubClient, repo: str, since: str, sample_size: int
) -> list[CommitSample]:
    """최근 1년 커밋에서 병합 커밋·봇 커밋을 뺀 표본을 모아 판정한다.

    병합 커밋을 빼는 이유: §4.2 ① 채굴 파이프라인이 병합 커밋을 건너뛰므로 기준을 맞춘다.
    봇 커밋을 빼는 이유: is_bot_commit 참고.
    표본을 쓰는 이유: 커밋마다 연결 PR 조회가 1회씩 들어 §7 한도(5,000회/시간)를 금방 쓴다.
    봇·병합 커밋이 많은 저장소에서도 사람 커밋을 채우려고 표본의 5배까지 훑는다.
    """
    raw_commits = client.paginate(
        f"/repos/{repo}/commits",
        {"since": since},
        max_items=sample_size * 5,
        max_pages=3,
    )
    samples: list[CommitSample] = []
    for commit in raw_commits:
        if len(commit.get("parents") or []) > 1 or is_bot_commit(commit):
            continue
        sha = commit.get("sha", "")
        message = (commit.get("commit") or {}).get("message", "")
        pulls = client.get_json(f"/repos/{repo}/commits/{sha}/pulls", allow_404=True) or []
        samples.append(summarize_commit(sha, message, pulls))
        if len(samples) >= sample_size:
            break
    return samples


def evaluate_repo(
    client: GitHubClient,
    full_name: str,
    *,
    since: str,
    sample_size: int,
    item: dict[str, Any] | None = None,
    enforce_pr_gate: bool = False,
) -> RepoRow:
    """저장소 하나를 §4.3 기준으로 평가한다. 비싼 호출은 앞 기준을 통과한 뒤에만 한다."""
    if item is None:
        item = client.get_json(f"/repos/{full_name}", allow_404=True)
        if item is None:
            return RepoRow(repo=full_name, exclude_reason="NOT_FOUND")

    row = _row_from_repo_item(full_name, item)

    if item.get("archived"):
        row.exclude_reason = "ARCHIVED"
        return row
    if item.get("fork"):
        row.exclude_reason = "FORK"
        return row
    language = item.get("language") or ""
    if language.lower() != "python":
        row.exclude_reason = f"LANGUAGE_NOT_PYTHON:{language or 'unknown'}"
        return row
    if not is_permissive_license(row.license_id):
        row.exclude_reason = f"LICENSE_NOT_PERMISSIVE:{row.license_id or 'none'}"
        return row

    head = client.get(f"/repos/{row.repo}/commits", {"per_page": 1})
    if head is None:
        row.exclude_reason = "NO_COMMITS"
        return row
    row.commits = commit_count_from_link(head.headers.get("link"), len(head.body or []))
    if row.commits < MIN_COMMITS:
        row.exclude_reason = f"COMMITS_BELOW_{MIN_COMMITS}:{row.commits}"
        return row

    samples = collect_commit_samples(client, row.repo, since, sample_size)
    row.sampled_commits = len(samples)
    if not samples:
        row.exclude_reason = "NO_RECENT_COMMITS"
        return row

    row.pr_ratio, row.issue_ref_ratio = compute_ratios(samples)
    row.score = score_candidate(row.pr_ratio, row.issue_ref_ratio, row.commits)
    if enforce_pr_gate and not row.pr_gate_pass:
        row.exclude_reason = f"PR_RATIO_BELOW_GATE:{row.pr_ratio:.3f}"
    return row


def search_repositories(
    client: GitHubClient, query: str, max_candidates: int
) -> list[dict[str, Any]]:
    """Search API 로 후보 목록을 받는다 (별 수 내림차순, GitHub 제한상 최대 1,000건)."""
    items: list[dict[str, Any]] = []
    page = 1
    while len(items) < max_candidates and page <= 10:
        body = client.get_json(
            "/search/repositories",
            {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": min(100, max_candidates),
                "page": page,
            },
        )
        page_items = (body or {}).get("items") or []
        if not page_items:
            break
        items.extend(page_items)
        page += 1
    return items[:max_candidates]


def sort_rows(rows: Sequence[RepoRow]) -> list[RepoRow]:
    """후보는 점수 내림차순, 제외된 저장소는 뒤에 이름순으로."""
    candidates = sorted(
        (row for row in rows if row.selected), key=lambda row: (-(row.score or 0.0), row.repo)
    )
    excluded = sorted((row for row in rows if not row.selected), key=lambda row: row.repo)
    return [*candidates, *excluded]


def write_csv(rows: Sequence[RepoRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_row())


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_query(min_stars: int) -> str:
    return f"language:Python is:public archived:false stars:>={min_stars}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.select_repos",
        description="CHARTER.md §4.3 기준으로 저장소 후보를 뽑아 점수순 CSV 로 쓴다.",
    )
    parser.add_argument("--out", type=Path, default=Path("docs/repo_candidates.csv"))
    parser.add_argument("--min-stars", type=int, default=1000, help="검색 하한 (기본 1000)")
    parser.add_argument("--max-candidates", type=int, default=100, help="평가할 검색 결과 수")
    parser.add_argument("--commit-sample", type=int, default=30, help="저장소당 커밋 표본 수")
    parser.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    parser.add_argument("--top", type=int, default=20, help="stdout 요약에 띄울 상위 개수")
    parser.add_argument("--query", default=None, help="검색 쿼리 직접 지정")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 호출")
    parser.add_argument("--no-seeds", action="store_true", help="§4.3 초기 후보 예시를 빼고 평가")
    parser.add_argument(
        "--enforce-pr-gate",
        action="store_true",
        help="PR 경유 비율이 게이트(0.70) 미만이면 후보에서 제외 (기본: 표시만)",
    )
    return parser


def resolve_cache_dir(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    return Path(os.environ.get("DATA_DIR", "./data")) / "github_cache"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_file(args.env_file)

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print(
            "GITHUB_TOKEN 이 없다. .env 에 채워라 (§7, §8.4). 토큰 없이는 시간당 60회뿐이다.",
            file=sys.stderr,
        )
        return 2

    def log(message: str) -> None:
        print(message, file=sys.stderr)

    client = GitHubClient(token, resolve_cache_dir(args.cache_dir), refresh=args.refresh, log=log)
    since = since_iso(args.since_days)
    query = args.query or build_query(args.min_stars)

    log(f"검색: {query}")
    items = search_repositories(client, query, args.max_candidates)
    log(f"검색 결과 {len(items)}개")

    targets: list[tuple[str, dict[str, Any] | None]] = [(item["full_name"], item) for item in items]
    seen = {name for name, _ in targets}
    if not args.no_seeds:
        targets.extend((name, None) for name in CHARTER_SEED_REPOS if name not in seen)

    rows: list[RepoRow] = []
    for index, (name, item) in enumerate(targets, start=1):
        try:
            row = evaluate_repo(
                client,
                name,
                since=since,
                sample_size=args.commit_sample,
                item=item,
                enforce_pr_gate=args.enforce_pr_gate,
            )
        except RuntimeError as error:  # 저장소 하나 때문에 전체가 멈추지 않게
            log(f"[{index}/{len(targets)}] {name} 실패: {error}")
            row = RepoRow(repo=name, exclude_reason=f"API_ERROR:{error}")
        rows.append(row)
        verdict = f"제외 {row.exclude_reason}" if row.exclude_reason else f"점수 {row.score:.4f}"
        log(f"[{index}/{len(targets)}] {name} {verdict}")

    ordered = sort_rows(rows)
    write_csv(ordered, args.out)

    candidates = [row for row in ordered if row.selected]
    log(f"API 호출 {client.api_calls}회 / 캐시 적중 {client.cache_hits}회")
    print(f"CSV: {args.out} (후보 {len(candidates)} / 전체 {len(ordered)})")
    print(f"상위 {min(args.top, len(candidates))}개 (점수순, §4.3 PR 게이트 70%):")
    for rank, row in enumerate(candidates[: args.top], start=1):
        gate = "PR-OK" if row.pr_gate_pass else "PR-LOW"
        print(
            f"{rank:>3}. {row.repo:<40} score={row.score:.4f} "
            f"pr={row.pr_ratio:.2f} issue={row.issue_ref_ratio:.2f} "
            f"commits={row.commits} {gate}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
