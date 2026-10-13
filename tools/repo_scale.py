"""저장소별 예상 규모·맥락 결합 API 부담 어림 (이슈 #171, ADR-020). 담당: 성제 (sj)

왜 만드나:
    저장소 100개를 노트북 3대에 나누려면(§7) 저장소마다 "얼마나 걸릴지"의 근거가 필요하다.
    20개 실행(#82)에서 시간은 커밋 수만으로 정해지지 않았고(초/커밋이 0.19~0.66으로 갈렸다),
    맥락 결합은 GitHub API 한도(5,000회/시간)가 시간을 정했다. 그래서 클론 없이 잴 수 있는
    값을 모은다. **선정에는 쓰지 않는다** — 선정은 `pipeline/select_repos.py` 가 한다.

무엇을 재나 (전부 채굴 구간 기준, 병합 커밋 제외 — §4.2 ① 파이프라인이 병합을 건너뛴다):
    - 연도별 커밋 수(GraphQL `history.totalCount`, 정확)와 그 해 최신 100개 커밋 표본
    - 대량 변경 커밋 비중: 표본 중 변경 파일 `BULK_FILES` 개 이상인 커밋의 비율,
      그 커밋들이 표본 변경 줄(추가+삭제)에서 차지하는 비율
    - PR 연결 커밋 수: 표본의 머지된 PR 연결 비율 × 연도별 커밋 수
    - 현재 트리의 `.py` 파일 수·바이트·가장 큰 파일 (Git Trees API, 1회)
    - 맥락 결합 API 호출 수 어림: 아래 "어림" 참고

어림 (정확한 값이 아니다):
    - 표본은 연도별 최신 100개라 그 해 후반부로 치우친다 (`select_repos.collect_year_samples`
      와 같은 한계). 연도별 커밋 수로 가중해 구간 값을 낸다.
    - 맥락 결합은 FULL_FUNCTION 레코드가 나온 커밋에만 API 를 쓴다 (#142). 그 커밋 수는 추출
      전에는 알 수 없어, 20개 실측의 "FULL_FUNCTION 커밋 / 순회 커밋" 비율(3.2%~12.6%, 중앙값
      7.5%)을 상수로 곱한다. 호출 수는 커밋당 `1(커밋→PR) + PR 연결 비율(리뷰 코멘트)
      + 이슈 참조 비율(이슈)` 로 본다. 저/중/고 세 값을 같이 낸다.
    - API `size` 는 GitHub 서버의 저장소 크기(KB)다. 실제 클론 크기와 다르다.

실행:
    python -m tools.repo_scale --input docs/repo_final100.csv --out docs/repo_final100_scale.csv

    GITHUB_TOKEN 은 .env 에서만 읽는다 (§8.4). GraphQL 응답은 `DATA_DIR/repo_scale_cache`,
    REST 응답은 `select_repos` 와 같은 캐시에 둔다. 파이프라인은 실행하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline import select_repos as sr

GRAPHQL_URL = "https://api.github.com/graphql"
YEAR_SAMPLE = 100  # 연도별 표본 수 (GraphQL 한 페이지)

# 대량 변경 커밋의 작업 정의. 프로젝트에 정해진 정의가 없어(NOISE_BULK 는 미구현,
# docs/filter_rules.md) 실행 계획용으로만 쓴다. 필터 규칙이 아니다.
BULK_FILES = 50

# 20개 실측(#82, data/run_*·data/context_*)의 FULL_FUNCTION 커밋 / 순회 커밋 비율.
# 최저·중앙값·최고 (docs/repo_final100.md §5.2 표).
FF_COMMIT_SHARE = (0.032, 0.075, 0.126)

# 표시용 규모 구분. 선정 기준이 아니다. XL 은 home-assistant/core 급(순회 커밋 5만 이상)이거나
# openpilot 급(서버 크기 2GB 이상)이다.
XL_COMMITS = 50_000
XL_SIZE_MB = 2_000
L_COMMITS = 20_000
L_SIZE_MB = 1_000

CSV_COLUMNS = (
    "repo",
    "scale_flag",
    "size_mb",
    "commits",
    "window_since_year",
    "window_commits_all",
    "est_walk_commits",
    "sampled_commits",
    "bulk_commit_share",
    "bulk_churn_share",
    "churn_mean",
    "py_files",
    "py_mb",
    "py_max_kb",
    "tree_truncated",
    "pr_linked_share",
    "est_pr_linked_commits",
    "prs_per_linked_commit",
    "est_ff_commits_mid",
    "est_api_calls_low",
    "est_api_calls_mid",
    "est_api_calls_high",
)

HISTORY_QUERY = """
query(
  $owner: String!, $name: String!, $ref: String!,
  $since: GitTimestamp!, $until: GitTimestamp!, $first: Int!
) {
  repository(owner: $owner, name: $name) {
    object(expression: $ref) {
      ... on Commit {
        history(first: $first, since: $since, until: $until) {
          totalCount
          nodes {
            oid
            additions
            deletions
            changedFilesIfAvailable
            parents { totalCount }
            associatedPullRequests(first: 3) { nodes { number mergedAt } }
          }
        }
      }
    }
  }
}
"""
# 큰 저장소는 100개 페이지에서 GitHub 가 502/504 를 낸다 (home-assistant/core 2015년에서 확인).
# 그때는 표본을 줄여 다시 묻는다.
YEAR_SAMPLE_FALLBACKS = (YEAR_SAMPLE, 30, 10)


# --------------------------------------------------------------------------------------
# 순수 계산 (테스트 대상)
# --------------------------------------------------------------------------------------


@dataclass
class YearSample:
    """한 해의 커밋 수(병합 포함, 정확)와 표본에서 센 값."""

    year: int
    total: int
    sampled: int = 0
    nonmerge: int = 0
    pr_linked: int = 0
    distinct_prs: int = 0
    bulk: int = 0
    churn: int = 0
    bulk_churn: int = 0


def summarize_year(year: int, total: int, nodes: Sequence[dict[str, Any]]) -> YearSample:
    """GraphQL 커밋 노드 목록 → 그 해 표본 요약. 병합 커밋은 분모에서 뺀다."""
    sample = YearSample(year=year, total=total, sampled=len(nodes))
    prs: set[int] = set()
    for node in nodes:
        if ((node.get("parents") or {}).get("totalCount") or 0) > 1:
            continue
        sample.nonmerge += 1
        churn = (node.get("additions") or 0) + (node.get("deletions") or 0)
        sample.churn += churn
        if (node.get("changedFilesIfAvailable") or 0) >= BULK_FILES:
            sample.bulk += 1
            sample.bulk_churn += churn
        pulls = (node.get("associatedPullRequests") or {}).get("nodes") or []
        merged = [pull["number"] for pull in pulls if pull and pull.get("mergedAt")]
        if merged:
            sample.pr_linked += 1
            prs.add(min(merged))
    sample.distinct_prs = len(prs)
    return sample


@dataclass
class WindowEstimate:
    """채굴 구간 어림. 비율은 표본이 없으면 None."""

    window_commits_all: int = 0
    sampled: int = 0
    est_walk_commits: int = 0
    bulk_commit_share: float | None = None
    bulk_churn_share: float | None = None
    churn_mean: float | None = None
    pr_linked_share: float | None = None
    est_pr_linked_commits: int = 0
    prs_per_linked_commit: float | None = None


def estimate_window(years: Sequence[YearSample]) -> WindowEstimate:
    """연도별 표본을 그 해 커밋 수로 가중해 구간 값을 낸다 (연도 층화)."""
    estimate = WindowEstimate()
    walk = linked = bulk = 0.0
    nonmerge = churn = bulk_churn = sample_linked = sample_prs = 0
    for year in years:
        estimate.window_commits_all += year.total
        estimate.sampled += year.nonmerge
        if not year.sampled:
            continue
        weight = year.total / year.sampled
        walk += weight * year.nonmerge
        linked += weight * year.pr_linked
        bulk += weight * year.bulk
        nonmerge += year.nonmerge
        churn += year.churn
        bulk_churn += year.bulk_churn
        sample_linked += year.pr_linked
        sample_prs += year.distinct_prs
    estimate.est_walk_commits = round(walk)
    estimate.est_pr_linked_commits = round(linked)
    if walk > 0:
        estimate.bulk_commit_share = bulk / walk
        estimate.pr_linked_share = linked / walk
    if nonmerge:
        estimate.churn_mean = churn / nonmerge
    if churn:
        estimate.bulk_churn_share = bulk_churn / churn
    if sample_linked:
        estimate.prs_per_linked_commit = sample_prs / sample_linked
    return estimate


def estimate_api_calls(
    walk_commits: int, pr_linked_share: float | None, issue_ref_ratio: float | None
) -> tuple[int, int, int, int]:
    """(FULL_FUNCTION 커밋 수 중간값, API 호출 저, 중, 고). 전부 어림이다.

    커밋당 호출 = 1 (커밋→PR 조회) + PR 연결 비율 (리뷰 코멘트 조회) + 이슈 참조 비율 (이슈 조회).
    같은 PR·이슈를 다시 부르면 캐시가 받으므로 실제는 이보다 적을 수 있다.
    """
    per_commit = 1.0 + (pr_linked_share or 0.0) + (issue_ref_ratio or 0.0)
    low, mid, high = (round(walk_commits * share * per_commit) for share in FF_COMMIT_SHARE)
    return round(walk_commits * FF_COMMIT_SHARE[1]), low, mid, high


def scale_flag(walk_commits: int, size_mb: float) -> str:
    """표시용 규모 구분 ("XL" / "L" / "")."""
    if walk_commits >= XL_COMMITS or size_mb >= XL_SIZE_MB:
        return "XL"
    if walk_commits >= L_COMMITS or size_mb >= L_SIZE_MB:
        return "L"
    return ""


def summarize_tree(tree: Sequence[dict[str, Any]]) -> tuple[int, int, int]:
    """(`.py` 파일 수, 바이트 합, 가장 큰 파일 바이트). 현재 트리만 본다."""
    sizes = [
        item.get("size") or 0
        for item in tree
        if item.get("type") == "blob" and str(item.get("path", "")).endswith(".py")
    ]
    return len(sizes), sum(sizes), max(sizes, default=0)


# --------------------------------------------------------------------------------------
# GitHub 호출
# --------------------------------------------------------------------------------------


Poster = Callable[[str, dict[str, str]], dict[str, Any]]


def _urlopen_post(payload: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(GRAPHQL_URL, payload.encode("utf-8"), headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


class GraphQLClient:
    """POST 전용 GraphQL 클라이언트. **오류 없는** 응답만 디스크에 캐시한다.

    GraphQL 은 HTTP 200 에 `errors` 와 부분 `data` 를 같이 줄 수 있다 (예: 타임아웃으로
    `history` 만 null). 그 응답을 캐시하면 그 해의 커밋 수가 0 으로 굳어 어림이 조용히 작아진다.
    그래서 `errors` 가 있으면 `data` 가 있어도 쓰지 않고 실패로 올리고, 이미 캐시에 있는
    그런 응답도 없는 것으로 본다 (#171 리뷰).
    """

    def __init__(
        self,
        token: str,
        cache_dir: Path,
        *,
        poster: Poster | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.token = token
        self.cache_dir = cache_dir
        self.poster = poster or _urlopen_post
        self.sleep = sleep
        self.log = log or (lambda message: None)
        self.api_calls = 0
        self.cache_hits = 0
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _read_cache(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict) or body.get("errors"):
            return None
        return body

    def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables}, sort_keys=True)
        path = self.cache_dir / f"{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}.json"
        cached = self._read_cache(path)
        if cached is not None:
            self.cache_hits += 1
            return cached
        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": sr.USER_AGENT,
            "Content-Type": "application/json",
        }
        for attempt in range(4):
            self.api_calls += 1
            try:
                body = self.poster(payload, headers)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
                # 502/504 는 질의가 무거워서 나는 것이라 같은 질의를 오래 붙잡지 않는다.
                if getattr(error, "code", None) in (502, 504) and attempt >= 1:
                    raise RuntimeError(f"GraphQL {error}") from error
                wait = self._retry_wait(error, attempt)
                self.log(f"GraphQL 재시도 ({error}), {wait}초 대기")
                self.sleep(wait)
                continue
            if body.get("errors"):
                kinds = {error.get("type") for error in body["errors"]}
                if "RATE_LIMITED" in kinds and attempt < 3:
                    self.log("GraphQL 한도 소진, 10분 대기")
                    self.sleep(600)
                    continue
                raise RuntimeError(f"GraphQL 오류: {body['errors']}")
            path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            return body
        raise RuntimeError(f"GraphQL 요청 실패: {variables}")

    @staticmethod
    def _retry_wait(error: Exception, attempt: int) -> int:
        headers = getattr(error, "headers", None)
        retry_after = headers.get("retry-after", "") if headers else ""
        if retry_after.isdigit():
            return int(retry_after) + 1
        if getattr(error, "code", None) in (403, 429):
            return 60 * (attempt + 1)
        return 2**attempt


def fetch_with_reconnect(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    """`select_repos` 의 기본 fetcher 에 연결 끊김 재시도를 얹는다.

    GitHub 가 응답 없이 연결을 닫으면 `http.client.RemoteDisconnected` 가 난다. `GitHubClient`
    는 `URLError` 만 재시도하므로 여기서 바꿔 올린다 (그 파일은 건드리지 않는다, #171).
    """
    try:
        return sr._urlopen_fetch(url, headers)
    except urllib.error.URLError:
        raise
    except (http.client.HTTPException, OSError) as error:
        raise urllib.error.URLError(error) from error


def collect_years(
    graphql: GraphQLClient, repo: str, branch: str, since_year: int, last_year: int
) -> list[YearSample]:
    owner, name = repo.split("/", 1)
    years: list[YearSample] = []
    step = 0
    for year in range(since_year, last_year + 1):
        variables = {
            "owner": owner,
            "name": name,
            "ref": f"refs/heads/{branch}",
            "since": f"{year}-01-01T00:00:00Z",
            "until": f"{year + 1}-01-01T00:00:00Z",
        }
        body: dict[str, Any] = {}
        while True:
            first = YEAR_SAMPLE_FALLBACKS[step]
            try:
                body = graphql.query(HISTORY_QUERY, {**variables, "first": first})
                break
            except RuntimeError as error:
                if step == len(YEAR_SAMPLE_FALLBACKS) - 1:
                    raise
                # 한 번 무거웠던 저장소는 다른 해도 무겁다. 남은 해는 줄인 표본으로 묻는다.
                step += 1
                graphql.log(f"{repo} {year}: 표본 {first}개 실패, 줄여서 재시도 ({error})")
        target = ((body.get("data") or {}).get("repository") or {}).get("object") or {}
        history = target.get("history") or {}
        nodes = history.get("nodes") or []
        years.append(summarize_year(year, history.get("totalCount") or 0, nodes))
    return years


def measure_repo(
    row: dict[str, str], graphql: GraphQLClient, rest: sr.GitHubClient, last_year: int
) -> dict[str, Any]:
    """선정 CSV 한 줄 → 규모 CSV 한 줄."""
    repo = row["repo"]
    branch = row["default_branch"]
    recent_only = row.get("recent_only", "").lower() == "true"
    # 파이프라인(`pipeline/run.py`)과 같은 규칙: recent_only 일 때만 구간을 좁힌다.
    since_year = int(row["mining_since_year"]) if recent_only else int(row["first_commit_year"])
    years = collect_years(graphql, repo, branch, since_year, last_year)
    window = estimate_window(years)

    tree_body = (
        rest.get_json(f"/repos/{repo}/git/trees/{branch}", {"recursive": 1}, allow_404=True) or {}
    )
    py_files, py_bytes, py_max = summarize_tree(tree_body.get("tree") or [])

    size_mb = int(row["size_kb"] or 0) / 1024
    issue_ref = float(row["issue_ref_ratio"]) if row.get("issue_ref_ratio") else None
    ff_mid, low, mid, high = estimate_api_calls(
        window.est_walk_commits, window.pr_linked_share, issue_ref
    )

    def num(value: float | None, digits: int = 3) -> str:
        return "" if value is None else f"{value:.{digits}f}"

    return {
        "repo": repo,
        "scale_flag": scale_flag(window.est_walk_commits, size_mb),
        "size_mb": f"{size_mb:.0f}",
        "commits": row["commits"],
        "window_since_year": since_year,
        "window_commits_all": window.window_commits_all,
        "est_walk_commits": window.est_walk_commits,
        "sampled_commits": window.sampled,
        "bulk_commit_share": num(window.bulk_commit_share),
        "bulk_churn_share": num(window.bulk_churn_share),
        "churn_mean": num(window.churn_mean, 0),
        "py_files": py_files,
        "py_mb": f"{py_bytes / 1024 / 1024:.1f}",
        "py_max_kb": f"{py_max / 1024:.0f}",
        "tree_truncated": "true" if tree_body.get("truncated") else "false",
        "pr_linked_share": num(window.pr_linked_share),
        "est_pr_linked_commits": window.est_pr_linked_commits,
        "prs_per_linked_commit": num(window.prs_per_linked_commit),
        "est_ff_commits_mid": ff_mid,
        "est_api_calls_low": low,
        "est_api_calls_mid": mid,
        "est_api_calls_high": high,
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.repo_scale",
        description="선정 CSV 의 저장소마다 예상 규모와 맥락 결합 API 부담을 어림한다.",
    )
    parser.add_argument("--input", type=Path, required=True, help="select_repos v2 형식 CSV")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--cache-dir", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sr.load_env_file(args.env_file)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("GITHUB_TOKEN 이 없다. .env 에 채워라 (§7, §8.4).", file=sys.stderr)
        return 2

    def log(message: str) -> None:
        print(message, file=sys.stderr)

    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    graphql = GraphQLClient(token, args.cache_dir or data_dir / "repo_scale_cache", log=log)
    rest = sr.GitHubClient(
        token,
        sr.resolve_cache_dir(None),
        max_wait_seconds=3900,
        fetcher=fetch_with_reconnect,
        log=log,
    )
    last_year = datetime.now(UTC).year

    with args.input.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("selection_status") != "EXCLUDED"]

    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        result = measure_repo(row, graphql, rest, last_year)
        results.append(result)
        log(
            f"[{index}/{len(rows)}] {row['repo']} 순회 커밋 약 {result['est_walk_commits']} "
            f"API 약 {result['est_api_calls_mid']} {result['scale_flag']}"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(results)
    log(f"GraphQL {graphql.api_calls}회 / 캐시 {graphql.cache_hits}회, REST {rest.api_calls}회")
    print(f"CSV: {args.out} ({len(results)}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
