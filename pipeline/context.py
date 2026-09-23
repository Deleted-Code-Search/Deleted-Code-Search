"""맥락 결합 — 커밋 하나에 PR·이슈·리뷰 코멘트를 붙인다. 담당: 희수 (hs)

무엇을:
    `(repo, commit_sha)` 를 받아 §4.4 `DeletionRecord.context` 를 채운다. 커밋이 속한 PR 을
    찾고, 커밋 메시지와 PR 본문의 이슈 참조를 풀어 제목·본문을 가져오고, 삭제된 파일에
    달린 PR 리뷰 코멘트를 모은다. §4.1 흐름도의 [맥락 결합] 단계다.

    §4.4 `replacement`(대체 코드) 도 같은 단계에서 채운다 (Issue #67). 삭제된 함수의 일을
    무엇이 이어받았는지를 같은 커밋의 추가 줄에서 찾는다 — 네트워크를 타지 않는 순수
    분석이라 위 수집과 성격이 다르지만, §4.2 ③ 이 둘을 같은 "맥락 결합" 단계로 묶는다.

왜 이 단계가 프로젝트의 급소인가:
    삭제 이유는 코드가 아니라 이 텍스트들에 적혀 있다. 여기서 붙는 텍스트의 양이 곧
    이유 회수율(§15)이고, 2주차 게이트 1(EXPLICIT+INFERRED ≥ 60%)의 상한을 정한다.
    그래서 회수율을 올리는 장치를 셋 넣었다 — 아래 "회수율" 주석 참고.

왜 GitHubClient 를 여기서 다시 만들지 않았나:
    캐시·레이트리밋 백오프·.env 로딩은 `pipeline/select_repos.py` (담당: 성제) 에 이미
    있다. 같은 것을 두 벌 두면 한도 관리가 갈린다. 재사용하되 그 파일은 건드리지 않았다.
    공용 모듈(`pipeline/github.py`) 로 빼는 편이 낫지만 남의 담당 파일을 쪼개는 일이라
    후속 이슈로 제안한다 (§8.6 담당 영역 경계).

실행:
    python -m pipeline.context --repo psf/requests --commit <sha>
    python -m pipeline.context --repo psf/requests --sample 20        # 붙는 비율 리포트
    python -m pipeline.context --input hunks.jsonl --out context.jsonl  # #5 출력 연결용

    GITHUB_TOKEN 은 .env 에서만 읽는다 (§8.4). 없으면 시간당 60회로 돌아간다.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline.parsers.base import Function
from pipeline.parsers.python_adapter import PythonAdapter
from pipeline.select_repos import (
    MERGE_TITLE_RE,
    SQUASH_SUFFIX_RE,
    GitHubClient,
    is_bot_commit,
    load_env_file,
    resolve_cache_dir,
    strip_pr_markers,
)

# 이슈 참조 1건마다 API 1회가 들어간다. 참조가 20개 달린 PR 도 있어 상한을 둔다.
MAX_ISSUES_PER_COMMIT = 5
MAX_REVIEW_COMMENTS = 50
# 리뷰 코멘트는 파일로 거르기 **전에** 넉넉히 받아야 한다 (fetch_review_comments 주석 참고).
MAX_REVIEW_PAGES = 2

# GitHub 가 이슈를 닫는 키워드 (close/fix/resolve 의 변화형). 이 참조는 "확실한 연관"으로 본다.
CLOSING_WORDS = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
CLOSING_REF_RE = re.compile(rf"\b{CLOSING_WORDS}\s*:?\s+#(\d+)\b", re.I)
# 참조 바로 앞이 닫기 키워드인가. `#12` 외의 형식(GH-12, owner/repo#12, URL)에도 쓴다.
CLOSING_PREFIX_RE = re.compile(rf"\b{CLOSING_WORDS}\s*:?\s+$", re.I)
# 앞에 단어문자·슬래시·#가 붙지 않은 #번호만. `owner/repo#1`, `##1` 을 걸러낸다.
PLAIN_REF_RE = re.compile(r"(?<![\w/#-])#(\d+)\b")
# `GH-1234` / `GH#1234` / `GH 1234`. pandas 가 이 형식만 쓴다 — 아래 "GH- 형식" 주석 참고.
GH_REF_RE = re.compile(r"\bGH[-#]?\s?(\d+)\b", re.I)
CROSS_REPO_REF_RE = re.compile(r"\b([\w.-]+/[\w.-]+)#(\d+)\b")
ISSUE_URL_RE = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# GitHubClient 는 한도 소진(403)·과다 요청(429)을 재시도한 뒤 "HTTP 403: {url}" 로 올린다.
RATE_LIMIT_STATUS = frozenset({403, 429})
HTTP_STATUS_RE = re.compile(r"HTTP (\d{3})")


# --------------------------------------------------------------------------------------
# 이슈 참조 파싱 (순수 함수 — 테스트 대상)
# --------------------------------------------------------------------------------------


def is_rate_limit_error(error: Exception) -> bool:
    """GitHubClient 가 올린 RuntimeError 가 한도 소진인가.

    GitHubClient(select_repos, 성제 담당)는 403/429 를 재시도한 뒤 네트워크 오류와 같은
    RuntimeError 로 올린다. 그 파일에 전용 예외를 넣는 게 맞지만 담당 영역 밖이라,
    여기서 메시지의 상태 코드로 가른다. 포맷이 바뀌면 테스트가 먼저 깨지게 해 뒀다.
    """
    match = HTTP_STATUS_RE.search(str(error))
    return bool(match) and int(match.group(1)) in RATE_LIMIT_STATUS


@dataclass(frozen=True, order=True)
class IssueRef:
    """본문에서 찾은 이슈 참조 하나."""

    number: int
    closing: bool = False
    """`fixes #12` 처럼 닫기 키워드로 참조됐나. 단순 `#12` 보다 연관이 확실하다."""


def strip_code(text: str) -> str:
    """마크다운 코드 블록·인라인 코드를 지운다.

    PR 본문의 코드 예시에 파이썬 주석(`#123`)이나 로그가 섞이면 이슈 참조로 오인된다.
    실제로 pandas PR 본문에서 `# 1234 rows` 같은 줄이 참조로 잡혔다.
    """
    return INLINE_CODE_RE.sub(" ", CODE_FENCE_RE.sub(" ", text))


def extract_issue_references(
    text: str | None,
    repo: str = "",
    exclude: Iterable[int] = (),
    *,
    strip_markers: bool = False,
) -> tuple[IssueRef, ...]:
    """본문에서 같은 저장소의 이슈 참조를 뽑는다.

    회수율 ①: `#12` 뿐 아니라 `fixes #12`, `owner/repo#12`, 이슈 URL, `GH-12` 까지 읽는다.
    정밀도: 다른 저장소 참조(`django/django#1`)와 코드 블록 안의 `#숫자` 는 버린다.
    `exclude` 에는 PR 자기 번호를 넣는다 (자기 자신은 이유의 근거가 아니다).

    `strip_markers` 는 **커밋 메시지에만** 켠다. 첫 줄의 머지·스쿼시 PR 번호를 지우는
    처리인데, PR 제목·본문에 켜면 `Follow-up to (#42)` 의 `#42` 까지 날아간다.

    GH- 형식을 넣은 이유:
        pandas 최근 PR 3건을 실제로 돌려 보니 본문에 `#숫자`가 한 번도 없고 `GH-66165` 만
        있었다. `#` 형식만 읽으면 pandas 에서 이슈 회수율이 0% 가 된다. pandas 는 §4.3
        초기 후보 저장소라 그대로 두면 게이트 1 측정이 왜곡된다.
    """
    if not text:
        return ()

    excluded = set(exclude)
    own_repo = repo.strip().lower()
    closing: set[int] = set()
    plain: set[int] = set()
    cleaned = strip_code(strip_pr_markers(text) if strip_markers else text)

    def add(number: int, *, is_closing: bool) -> None:
        (closing if is_closing else plain).add(number)

    def closing_before(match: re.Match[str]) -> bool:
        """참조 바로 앞이 `Fixes ` 같은 닫기 키워드인가.

        `#12` 는 CLOSING_REF_RE 가 한 번에 잡지만 `GH-12`·`owner/repo#12`·URL 은 형식이
        달라 따로 본다. 닫기 참조를 놓치면 정렬에서 뒤로 밀려 max_issues 에 잘린다.
        """
        return bool(
            CLOSING_PREFIX_RE.search(match.string[max(0, match.start() - 24) : match.start()])
        )

    def take_url(match: re.Match[str]) -> str:
        owner, name, number = match.group(1), match.group(2), match.group(3)
        if own_repo and f"{owner}/{name}".lower() == own_repo:
            add(int(number), is_closing=closing_before(match))
        return " "

    def take_cross(match: re.Match[str]) -> str:
        target, number = match.group(1).lower(), match.group(2)
        if own_repo and target == own_repo:
            add(int(number), is_closing=closing_before(match))
        return " "

    # 처리한 참조는 공백으로 지운다. 남겨 두면 PLAIN_REF_RE 가 다시 잡는다.
    cleaned = ISSUE_URL_RE.sub(take_url, cleaned)
    cleaned = CROSS_REPO_REF_RE.sub(take_cross, cleaned)

    closing.update(int(match.group(1)) for match in CLOSING_REF_RE.finditer(cleaned))
    plain.update(int(match.group(1)) for match in PLAIN_REF_RE.finditer(cleaned))
    for match in GH_REF_RE.finditer(cleaned):
        add(int(match.group(1)), is_closing=closing_before(match))

    closing -= excluded
    plain -= excluded | closing
    return (
        *(IssueRef(number, True) for number in sorted(closing)),
        *(IssueRef(number, False) for number in sorted(plain)),
    )


def merge_refs(*groups: Iterable[IssueRef]) -> tuple[IssueRef, ...]:
    """여러 본문에서 모은 참조를 합친다. 같은 번호면 닫기 키워드 쪽을 남기고, 닫기 참조가 앞."""
    best: dict[int, bool] = {}
    for group in groups:
        for ref in group:
            best[ref.number] = best.get(ref.number, False) or ref.closing
    return tuple(
        sorted((IssueRef(number, closing) for number, closing in best.items()), key=_ref_order)
    )


def _ref_order(ref: IssueRef) -> tuple[int, int]:
    return (0 if ref.closing else 1, ref.number)


def pr_number_from_message(message: str) -> int | None:
    """커밋 메시지 첫 줄의 머지·스쿼시 마커에서 PR 번호를 읽는다.

    회수율 ②: `/commits/{sha}/pulls` 가 비는 커밋이 있다. 스쿼시 머지로 SHA 가 바뀌었거나
    기본 브랜치 밖에서 들어온 경우다. 그때 "Merge pull request #123" / "... (#123)" 을
    폴백으로 쓴다.
    """
    first_line = (message.splitlines() or [""])[0]
    match = MERGE_TITLE_RE.search(first_line) or SQUASH_SUFFIX_RE.search(first_line)
    return int(match.group(1)) if match else None


# --------------------------------------------------------------------------------------
# 결과 형태 (§4.4 context 매핑)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewComment:
    """PR 리뷰의 인라인 코멘트 하나."""

    body: str
    path: str = ""
    line: int | None = None
    author: str = ""


@dataclass
class CommitContext:
    """커밋 하나에 붙은 맥락. §4.4 `DeletionRecord.context` 의 입력이 된다."""

    repo: str
    commit_sha: str
    commit_message: str = ""
    pr_number: int | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    issue_refs: tuple[IssueRef, ...] = ()
    issue_titles: tuple[str, ...] = ()
    review_comments: tuple[ReviewComment, ...] = ()
    # §4.4 에 칸이 없는 값. 분류(§4.2 ③)에 쓸모가 있어 모으기는 하되 스키마에는 넣지 않는다.
    # 스키마에 추가할지는 §13 절차(이슈 → 회의 → ADR)로 정한다. PR 본문에 변경 제안으로 적었다.
    pr_labels: tuple[str, ...] = ()
    issue_bodies: tuple[str, ...] = ()

    @property
    def issue_numbers(self) -> tuple[int, ...]:
        return tuple(ref.number for ref in self.issue_refs)

    @property
    def has_pr(self) -> bool:
        return self.pr_number is not None

    @property
    def has_issue(self) -> bool:
        return bool(self.issue_refs)

    @property
    def has_review(self) -> bool:
        return bool(self.review_comments)

    @property
    def has_any_context(self) -> bool:
        """커밋 메시지 말고 붙은 게 하나라도 있나. 이유 회수율(§15)의 상한 지표."""
        return self.has_pr or self.has_issue or self.has_review

    def to_schema_context(self) -> dict[str, Any]:
        """§4.4 `context` 필드 그대로. 필드를 늘리거나 이름을 바꾸지 않는다 (§13)."""
        return {
            "commit_message": self.commit_message,
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
            "pr_body": self.pr_body,
            "issue_numbers": list(self.issue_numbers),
            "issue_titles": list(self.issue_titles),
            "review_comments": [comment.body for comment in self.review_comments],
        }


@dataclass
class AttachmentReport:
    """샘플 여러 건에 대해 "맥락이 붙은 비율". 게이트 1 준비용 (§9 2주차)."""

    total: int = 0
    with_pr: int = 0
    with_issue: int = 0
    with_review: int = 0
    with_any: int = 0
    api_calls: int = 0
    cache_hits: int = 0
    stopped_reason: str = ""
    """비어 있지 않으면 표본을 다 돌기 전에 멈춘 것. 비율을 전체 표본 값으로 읽으면 안 된다."""
    skipped: dict[str, int] = field(default_factory=dict)
    """건너뛴 조회 종류별 횟수. 실패를 조용히 삼키면 무엇을 잃었는지 모른다 (#50)."""

    def add(self, context: CommitContext) -> None:
        self.total += 1
        self.with_pr += int(context.has_pr)
        self.with_issue += int(context.has_issue)
        self.with_review += int(context.has_review)
        self.with_any += int(context.has_any_context)

    def _ratio(self, count: int) -> float:
        return count / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "with_pr": self.with_pr,
            "with_issue": self.with_issue,
            "with_review": self.with_review,
            "with_any": self.with_any,
            "pr_rate": round(self._ratio(self.with_pr), 4),
            "issue_rate": round(self._ratio(self.with_issue), 4),
            "review_rate": round(self._ratio(self.with_review), 4),
            "any_rate": round(self._ratio(self.with_any), 4),
            "api_calls": self.api_calls,
            "cache_hits": self.cache_hits,
            "stopped_reason": self.stopped_reason,
            "skipped": dict(self.skipped),
        }

    def format_lines(self) -> list[str]:
        data = self.as_dict()
        lines = [
            f"표본 {data['total']}건",
            f"  PR 붙음      {data['with_pr']:>4} ({data['pr_rate']:.1%})",
            f"  이슈 붙음    {data['with_issue']:>4} ({data['issue_rate']:.1%})",
            f"  리뷰 붙음    {data['with_review']:>4} ({data['review_rate']:.1%})",
            f"  하나라도     {data['with_any']:>4} ({data['any_rate']:.1%})",
            f"  API {data['api_calls']}회 / 캐시 {data['cache_hits']}회",
        ]
        if self.skipped:
            detail = ", ".join(f"{kind} {count}회" for kind, count in sorted(self.skipped.items()))
            lines.append(f"  건너뛴 조회: {detail} (그 조각만 빠지고 나머지 맥락은 남았다)")
        if self.stopped_reason:
            lines.append(f"  ** 중단: {self.stopped_reason} — 위 비율은 돌린 만큼의 값이다 **")
        return lines


# --------------------------------------------------------------------------------------
# 수집
# --------------------------------------------------------------------------------------


@dataclass
class ContextCollector:
    """커밋 → 맥락. 모든 GitHub 호출은 캐시를 거치는 `GitHubClient` 로만 나간다."""

    client: GitHubClient
    max_issues: int = MAX_ISSUES_PER_COMMIT
    max_review_comments: int = MAX_REVIEW_COMMENTS
    seen_shas: set[str] = field(default_factory=set)
    skipped: dict[str, int] = field(default_factory=dict)
    """건너뛴 조회 종류별 횟수. 조용히 삼키면 무엇을 잃었는지 모른다 — 아래 주석 참고."""

    def _tolerate(self, kind: str, error: RuntimeError) -> None:
        """조회 하나가 실패했을 때 그 조각만 포기한다. 단 한도 소진은 올려보낸다.

        왜 삼키나:
            조회 하나가 실패하면 `collect` 전체가 터지고, `run_targets` 가 빈 맥락을 기록해
            **이미 찾아 둔 PR 제목·본문까지 같이 버려졌다.** 삭제된 이슈(410)에서 실제로
            일어났고, httpx 표본 50건 중 21건(42%)이 이것 때문에 날아갔다 (#47, #50).
            이슈를 활발히 참조하는 저장소일수록 삭제된 이슈도 많아, 이유가 잘 적힌 저장소가
            더 손해를 보는 구조였다.

        왜 한도 소진만 예외인가:
            한도가 바닥난 뒤 조용히 넘어가면 남은 커밋이 전부 "맥락 없음" 으로 기록돼
            회수율이 거짓으로 낮아진다. 측정 실패를 측정 결과로 착각하게 되는 같은 종류의
            사고다 (#33 에서 넣은 보호).
        """
        if is_rate_limit_error(error):
            raise error
        self.skipped[kind] = self.skipped.get(kind, 0) + 1

    def get_json(self, kind: str, path: str) -> Any:
        """실패를 견디는 단건 조회. 실패하면 None 이고 그 사실이 `skipped` 에 남는다."""
        try:
            return self.client.get_json(path, allow_404=True)
        except RuntimeError as error:
            self._tolerate(kind, error)
            return None

    def paginate(self, kind: str, path: str, **kwargs: Any) -> list[Any]:
        """실패를 견디는 페이지 조회. 실패하면 빈 목록."""
        try:
            return self.client.paginate(path, **kwargs)
        except RuntimeError as error:
            self._tolerate(kind, error)
            return []

    def fetch_commit_message(self, repo: str, commit_sha: str) -> str:
        commit = self.get_json("commit", f"/repos/{repo}/commits/{commit_sha}")
        return ((commit or {}).get("commit") or {}).get("message") or ""

    def find_pull_request(
        self, repo: str, commit_sha: str, commit_message: str
    ) -> dict[str, Any] | None:
        """커밋을 담은 PR. 머지된 것만 인정한다.

        열린 PR 은 아직 기본 브랜치에 들어오지 않았으므로 이 삭제의 경로가 아니다.
        여러 개면 가장 먼저 머지된 것을 원본으로 본다 (뒤의 것은 백포트·체리픽).
        """
        pulls = self.get_json("pr", f"/repos/{repo}/commits/{commit_sha}/pulls")
        merged = [pull for pull in (pulls or []) if pull.get("merged_at")]
        if merged:
            return min(
                merged, key=lambda pull: (pull.get("merged_at") or "", pull.get("number", 0))
            )

        number = pr_number_from_message(commit_message)
        if number is None:
            return None
        pull = self.get_json("pr", f"/repos/{repo}/pulls/{number}")
        return pull if pull and pull.get("merged_at") else None

    def fetch_issue(self, repo: str, number: int) -> dict[str, Any] | None:
        """이슈 하나. PR 번호였으면 None.

        정밀도: `/issues/{n}` 는 PR 도 돌려준다 (GitHub 에서 PR 은 이슈의 특수형).
        `pull_request` 키가 있으면 이슈가 아니므로 버린다. 이걸 안 하면 "관련 PR 번호"가
        이슈로 둔갑해 issue_titles 에 PR 제목이 섞인다.

        삭제된 이슈는 404 가 아니라 **410 Gone** 이다. 그래서 `allow_404` 로는 안 걸리고
        예외가 되어 커밋 전체를 날렸다 (#50). 이제 그 이슈만 건너뛴다.
        """
        issue = self.get_json("issue", f"/repos/{repo}/issues/{number}")
        if not issue or issue.get("pull_request"):
            return None
        return issue

    def fetch_review_comments(
        self, repo: str, pr_number: int, file_path: str | None = None
    ) -> tuple[ReviewComment, ...]:
        """PR 인라인 리뷰 코멘트. `file_path` 를 주면 그 파일에 달린 것만 (§4.2 맥락 결합).

        회수율 ③: 파일 코멘트가 하나도 없으면 PR 전체 코멘트로 되돌아간다. 리뷰어가
        다른 파일 줄에 "이건 왜 지웠나" 를 적는 일이 흔해서, 빈손보다 낫다.

        상한을 거르기 **전**이 아니라 **후**에 거는 이유:
            paginate 는 `max_items` 를 채우면 멈춘다. 예전처럼 `max_items=50` 으로 부르면
            첫 페이지 50건에서 끊겨, 51번째에 있던 대상 파일 코멘트를 영영 못 본다.
            그 상태로 파일 필터가 빈손이 되면 폴백이 무관한 50건을 통째로 돌려준다.
            그래서 두 페이지를 먼저 받고, 파일로 거른 뒤, 남은 것에 상한을 건다.
        """
        raw = self.paginate(
            "review",
            f"/repos/{repo}/pulls/{pr_number}/comments",
            max_items=MAX_REVIEW_PAGES * 100,
            max_pages=MAX_REVIEW_PAGES,
        )
        parsed = [
            ReviewComment(
                body=(item.get("body") or "").strip(),
                path=item.get("path") or "",
                line=item.get("line") or item.get("original_line"),
                author=(item.get("user") or {}).get("login") or "",
            )
            for item in raw
            if (item.get("body") or "").strip()
        ]
        if file_path:
            on_file = [comment for comment in parsed if comment.path == file_path]
            if on_file:
                return tuple(on_file[: self.max_review_comments])
        return tuple(parsed[: self.max_review_comments])

    def collect(
        self,
        repo: str,
        commit_sha: str,
        *,
        file_path: str | None = None,
        commit_message: str | None = None,
    ) -> CommitContext:
        """커밋 하나의 맥락을 모은다. `commit_message` 를 주면 커밋 조회 1회를 아낀다."""
        if commit_message is None:
            commit_message = self.fetch_commit_message(repo, commit_sha)
        self.seen_shas.add(commit_sha)

        context = CommitContext(repo=repo, commit_sha=commit_sha, commit_message=commit_message)
        pull = self.find_pull_request(repo, commit_sha, commit_message)
        exclude: list[int] = []

        if pull:
            context.pr_number = pull.get("number")
            context.pr_title = pull.get("title")
            context.pr_body = pull.get("body")
            context.pr_labels = tuple(
                label.get("name", "") for label in (pull.get("labels") or []) if label.get("name")
            )
            if context.pr_number is not None:
                exclude.append(context.pr_number)

        refs = merge_refs(
            # 머지·스쿼시 마커 제거는 커밋 메시지에만. PR 제목·본문에 걸면 참조가 날아간다.
            extract_issue_references(commit_message, repo, exclude, strip_markers=True),
            extract_issue_references(context.pr_title, repo, exclude),
            extract_issue_references(context.pr_body, repo, exclude),
        )

        resolved: list[IssueRef] = []
        titles: list[str] = []
        bodies: list[str] = []
        for ref in refs[: self.max_issues]:
            issue = self.fetch_issue(repo, ref.number)
            if issue is None:
                continue
            resolved.append(ref)
            titles.append(issue.get("title") or "")
            bodies.append(issue.get("body") or "")

        context.issue_refs = tuple(resolved)
        context.issue_titles = tuple(titles)
        context.issue_bodies = tuple(bodies)

        if context.pr_number is not None:
            context.review_comments = self.fetch_review_comments(repo, context.pr_number, file_path)
        return context


# --------------------------------------------------------------------------------------
# 대체 코드 매칭 (§4.2 ③, §4.4 `replacement`, Issue #67)
# --------------------------------------------------------------------------------------

# §4.4 `replacement.match_method` enum. 이 셋 밖의 값을 쓰지 않는다 (§13).
MATCH_SAME_LOCATION = "SAME_LOCATION"
MATCH_CALLER_CHANGE = "CALLER_CHANGE"
MATCH_NONE = "NONE"

# 같은 이름 함수가 하나만 추가됐을 때. 1.0 이 아닌 이유는 `match_replacement` 독스트링 참고.
SAME_NAME_CONFIDENCE = 0.9
# 같은 이름 후보가 여럿이라 유사도로 고른 경우. 위치가 아니라 본문으로 고른 것이라 낮춘다.
AMBIGUOUS_NAME_CONFIDENCE = 0.7

# 추출 출력의 추가 줄 필드. 헝크 단위 새 이름이 있으면 그것을 쓰고, 없으면 옛 평탄한 문자열을
# 읽는다 (2026-09-23 팀 확정). 예비 200건이 옛 형식으로 이미 뽑혀 있어 둘 다 읽어야 한다.
ADDED_HUNKS_FIELD = "added_hunks_same_file"
ADDED_HUNK_FIELD = "added_hunk_same_file"

# 파서는 상태가 없어 하나만 만들어 쓴다 (`extract.py` 와 같은 방식).
_ADAPTER = PythonAdapter()


def added_hunk_text(record: dict[str, Any]) -> str | None:
    """그 커밋·그 파일에서 추가된 줄 전체. 필드가 아예 없으면 `None`.

    `None`(필드 없음)과 `""`(필드는 있고 추가 줄이 0개)를 **구별해서 돌려준다.** 앞은
    "모른다", 뒤는 "대체가 없다"이고 둘은 다른 판정으로 간다. 라벨 가이드 §6.2.3 이
    `replacement` 가 `null` 일 때 "수집되지 않은 것과 존재하지 않는 것을 이 필드로 구별할
    수 없다"고 경고한 것이 이 구별을 잃었을 때 벌어지는 일이다.
    """
    hunks = record.get(ADDED_HUNKS_FIELD)
    if hunks is not None:
        return "\n".join(body for hunk in hunks if (body := (hunk or {}).get("added_body")))
    flat = record.get(ADDED_HUNK_FIELD)
    return flat if flat is None else str(flat)


def _added_functions(text: str) -> list[Function]:
    """추가 줄 텍스트에서 완결된 함수만 뽑는다.

    이 텍스트는 diff 의 추가 줄만 이어 붙인 것이라 **온전한 파이썬 소스가 아니다.**
    여기저기 흩어진 수정이면 들여쓰기가 끊겨 구문이 깨진다. tree-sitter 는 오류에
    관대해서 깨진 자리를 건너뛰고 완결된 `function_definition` 만 돌려주므로, 그
    성질에 기댄다 - 완결되지 않은 조각은 애초에 대체 코드 후보가 아니다.
    """
    try:
        return list(_ADAPTER.extract_functions(text))
    except Exception:  # noqa: BLE001 - 파서가 어떤 예외를 낼지는 어댑터 구현에 달렸다
        return []


def _similarity(deleted_body: str, candidate: Function) -> float:
    """정규화 본문끼리의 유사도. 같은 이름 후보가 여럿일 때 고르는 데만 쓴다.

    `filter.normalize_function_body` 를 그대로 쓴다 (ADR-014) - 변수명·리터럴을 치워야
    "같은 일을 하는 코드"가 붙는다. `filter._move_similarity` 를 쓰지 않는 이유는 그쪽이
    이동 판정용이라 0.9 미만을 `None` 으로 잘라 내기 때문이다. 대체 코드는 다시 쓰인
    코드라 0.9 를 넘는 일이 드물어, 여기서는 자르지 않고 순위만 매긴다.
    """
    from pipeline.filter import normalize_function_body

    return difflib.SequenceMatcher(
        None, normalize_function_body(deleted_body), normalize_function_body(candidate.body)
    ).ratio()


@dataclass(frozen=True)
class Replacement:
    """§4.4 `DeletionRecord.replacement`. 삭제된 함수의 일을 무엇이 이어받았나."""

    code: str | None
    match_method: str | None
    confidence: float

    def to_schema_replacement(self) -> dict[str, Any]:
        """§4.4 `replacement` 필드 그대로. 이름을 바꾸거나 늘리지 않는다 (§13)."""
        return {
            "code": self.code,
            "match_method": self.match_method,
            "confidence": self.confidence,
        }


#: 후보를 못 찾았거나 판정할 수 없을 때. `match_method` 가 `None` 인 것은 §4.4 enum 밖의
#: 값을 지어내지 않겠다는 뜻이다 - `NONE` 은 "대체가 없다"는 **적극적 판정**이라 여기 쓸 수 없다.
UNDETERMINED = Replacement(code=None, match_method=None, confidence=0.0)


def match_replacement(record: dict[str, Any]) -> Replacement:
    """레코드 하나의 대체 코드를 찾는다 (§4.2 ③ "삭제 위치 ±N줄 내 추가된 함수/블록").

    **지금은 줄 번호가 없어 위치로 판정하지 못한다.** 추출 출력의 추가 줄 필드가 파일
    단위로 평탄화돼 있어(`added_hunk_same_file`), 부모 파일의 `start_line` 과 이어 붙일
    좌표가 남지 않는다. 헝크 단위 출력이 붙으면 그때 위치로 판정한다.

    그래서 지금 판정하는 것은 두 경우뿐이다.

    - **추가 줄이 하나도 없다** -> `NONE`. 그 커밋이 그 파일에 아무것도 안 넣었으므로
      대체 코드가 없다는 것이 확실하다
    - **삭제된 함수와 같은 이름의 함수가 추가됐다** -> `SAME_LOCATION`, 신뢰도
      `SAME_NAME_CONFIDENCE`. 이동 필터(§4.2 ②)가 **"이 파일 다른 자리로 옮겨 간 것"을 이미
      빼고 남긴 레코드**라(`filter.partition_moved`), 같은 커밋·같은 파일에 같은 이름이
      다시 나타났다면 제자리 교체로 본다. 1.0 이 아닌 것은 잔여 가능성 때문이다 - 본문이
      크게 다시 쓰이면서 자리도 옮긴 경우는 유사도 0.9 에 걸리지 않아 이동으로 안 잡히고
      여기까지 온다

    나머지(이름이 다른 함수가 추가됐거나, 추가는 있는데 완결된 함수가 없는 부분 수정)는
    `UNDETERMINED` 로 둔다. 후보를 억지로 고르지 않는 이유는 그것이 게이트 1 숫자를
    부풀리기 때문이다 - 라벨 가이드 §6.2.1 이 `replacement.code` 를 INFERRED 근거 ①(신뢰도
    0.8~1.0 구간)로 쓰므로, 아닌 것을 채우면 **없는 근거로 회수율이 올라간다.**
    """
    text = added_hunk_text(record)
    if text is None:
        return UNDETERMINED
    if not text.strip():
        return Replacement(code=None, match_method=MATCH_NONE, confidence=0.0)

    name = record.get("function_name")
    same_name = [f for f in _added_functions(text) if f.name == name]
    if not same_name:
        return UNDETERMINED
    if len(same_name) == 1:
        return Replacement(same_name[0].body, MATCH_SAME_LOCATION, SAME_NAME_CONFIDENCE)

    # 한 파일에 같은 이름 함수가 여럿 추가될 수 있다 (`__init__` 등). 어느 것이 이 함수를
    # 이어받았는지는 위치 없이는 확정할 수 없어, 본문이 가장 가까운 것을 고르고 신뢰도를 낮춘다.
    best = max(
        same_name, key=lambda f: (_similarity(record.get("deleted_body") or "", f), -f.start_line)
    )
    return Replacement(best.body, MATCH_SAME_LOCATION, AMBIGUOUS_NAME_CONFIDENCE)


# --------------------------------------------------------------------------------------
# 입력 (#5 출력 연결 / 직접 표본)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitTarget:
    """맥락을 붙일 대상 하나. §4.2 ① 채굴 파이프라인 출력의 부분집합."""

    repo: str
    commit_sha: str
    file_path: str | None = None
    commit_message: str | None = None
    record: dict[str, Any] | None = None
    """#5 가 준 원본 JSONL 레코드. 출력에서 그대로 돌려주려고 들고 있는다."""


def parse_targets(lines: Iterable[str]) -> list[CommitTarget]:
    """#5(삭제 헝크 추출) 의 JSONL 출력을 읽는다.

    §4.2 ① 출력 키(`repo`, `commit_sha`, `file_path`, `commit_message`)만 본다. 나머지 키가
    있어도 무시하므로, #5 스키마가 확정되기 전에도 맞물린다.
    """
    targets: list[CommitTarget] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        record = json.loads(line)
        repo, sha = record.get("repo"), record.get("commit_sha")
        if not repo or not sha:
            continue
        targets.append(
            CommitTarget(
                repo=repo,
                commit_sha=sha,
                file_path=record.get("file_path"),
                commit_message=record.get("commit_message"),
                record=record,
            )
        )
    return targets


def build_output_record(target: CommitTarget, context: CommitContext) -> dict[str, Any]:
    """출력 한 줄. #5 가 준 레코드를 그대로 두고 `context` 만 채워 넣는다.

    원본을 버리면 `deleted_hunk`·`file_path` 가 사라져 §4.4 레코드를 다시 조립할 수 없다.
    한 커밋에 삭제 파일이 여럿이면 `file_path` 없이는 어느 줄이 어느 파일인지도 모른다.
    """
    record = dict(target.record or {})
    record["repo"] = context.repo
    record["commit_sha"] = context.commit_sha
    if target.file_path is not None:
        record.setdefault("file_path", target.file_path)
    record["context"] = context.to_schema_context()
    record["replacement"] = match_replacement(record).to_schema_replacement()
    return record


def sample_targets(client: GitHubClient, repo: str, count: int) -> list[CommitTarget]:
    """최근 커밋에서 표본을 뽑는다 (#5 없이 붙는 비율을 재기 위한 임시 입력).

    병합·봇 커밋은 뺀다. §4.2 ① 채굴 파이프라인이 병합 커밋을 건너뛰므로 기준을 맞췄고,
    봇 커밋은 PR 없이 직접 밀어 넣어 비율을 왜곡한다 (select_repos 와 같은 이유).
    """
    raw = client.paginate(f"/repos/{repo}/commits", max_items=count * 4, max_pages=3)
    targets: list[CommitTarget] = []
    for commit in raw:
        if len(commit.get("parents") or []) > 1 or is_bot_commit(commit):
            continue
        targets.append(
            CommitTarget(
                repo=repo,
                commit_sha=commit.get("sha", ""),
                commit_message=(commit.get("commit") or {}).get("message", ""),
            )
        )
        if len(targets) >= count:
            break
    return targets


def run_targets(
    collector: ContextCollector,
    targets: Sequence[CommitTarget],
    *,
    log: object = None,
) -> tuple[list[CommitContext], AttachmentReport]:
    """표본 전체에 맥락 결합을 돌리고 붙는 비율을 센다.

    네트워크 오류 한 건에 멈추지 않는다. 20건 중 19건을 잃으면 다시 돌릴 때 그만큼 한도를
    또 쓴다 (select_repos 가 저장소 단위로 쓰는 것과 같은 보호). 실패한 건은 맥락 없이
    기록돼 비율에 정직하게 반영된다.

    단, 한도 소진은 다르게 다룬다. 그대로 두면 남은 커밋이 전부 "맥락 없음"으로 기록돼
    JSONL 에 멀쩡한 결과처럼 남고, 이유 회수율이 실제보다 낮게 나온다 (측정 실패를 측정
    결과로 착각하게 된다). 그래서 그 자리에서 멈추고, 멈춘 대상은 아예 기록하지 않으며,
    리포트에 이유를 남긴다. 그때까지 모은 것은 버리지 않는다.
    """
    contexts: list[CommitContext] = []
    report = AttachmentReport()
    for index, target in enumerate(targets, start=1):
        try:
            context = collector.collect(
                target.repo,
                target.commit_sha,
                file_path=target.file_path,
                commit_message=target.commit_message,
            )
            marks = "".join(
                mark if flag else "-"
                for mark, flag in (
                    ("P", context.has_pr),
                    ("I", context.has_issue),
                    ("R", context.has_review),
                )
            )
        except RuntimeError as error:
            if is_rate_limit_error(error):
                remaining = len(targets) - index + 1
                report.stopped_reason = f"API 한도 소진 ({remaining}건 남기고 중단)"
                if callable(log):
                    log(f"[{index}/{len(targets)}] {report.stopped_reason}: {error}")
                break
            context = CommitContext(
                repo=target.repo,
                commit_sha=target.commit_sha,
                commit_message=target.commit_message or "",
            )
            marks = f"실패 {error}"
        contexts.append(context)
        report.add(context)
        if callable(log):
            log(f"[{index}/{len(targets)}] {target.commit_sha[:10]} {marks}")
    report.api_calls = collector.client.api_calls
    report.cache_hits = collector.client.cache_hits
    report.skipped = dict(collector.skipped)
    return contexts, report


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.context",
        description="커밋에 PR·이슈·리뷰 코멘트를 붙인다 (CHARTER.md §4.2 맥락 결합).",
    )
    parser.add_argument("--repo", default=None, help="owner/name")
    parser.add_argument("--commit", default=None, help="커밋 SHA 하나")
    parser.add_argument("--file-path", default=None, help="리뷰 코멘트를 이 파일로 좁힌다")
    parser.add_argument("--sample", type=int, default=0, help="최근 커밋 N건에 적용해 비율 측정")
    parser.add_argument("--input", type=Path, default=None, help="#5 출력 JSONL")
    parser.add_argument("--out", type=Path, default=None, help="결과 JSONL 저장 경로")
    parser.add_argument("--max-issues", type=int, default=MAX_ISSUES_PER_COMMIT)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 호출")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_file(args.env_file)
    # 리포트가 한글이라 Windows 기본 콘솔(cp949)에서 깨진다. 팀 전원이 Windows 다.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    def log(message: str) -> None:
        print(message, file=sys.stderr)

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        log("GITHUB_TOKEN 이 없다 (.env, §8.4). 시간당 60회로 돈다.")

    client = GitHubClient(token, resolve_cache_dir(args.cache_dir), refresh=args.refresh, log=log)
    collector = ContextCollector(client, max_issues=args.max_issues)

    if args.input:
        targets = parse_targets(args.input.read_text(encoding="utf-8").splitlines())
    elif args.sample:
        if not args.repo:
            log("--sample 에는 --repo 가 필요하다.")
            return 2
        targets = sample_targets(client, args.repo, args.sample)
    elif args.repo and args.commit:
        targets = [CommitTarget(args.repo, args.commit, args.file_path)]
    else:
        log("--repo/--commit, --repo/--sample, --input 중 하나를 줘라.")
        return 2

    if not targets:
        log("대상이 없다.")
        return 1

    contexts, report = run_targets(collector, targets, log=log if len(targets) > 1 else None)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as handle:
            for target, context in zip(targets, contexts, strict=False):
                record = build_output_record(target, context)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"JSONL: {args.out} ({len(contexts)}건)")

    if len(contexts) == 1:
        print(json.dumps(contexts[0].to_schema_context(), ensure_ascii=False, indent=2))
    for line in report.format_lines():
        print(line)
    return 1 if report.stopped_reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
