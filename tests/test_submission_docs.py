"""제출 문서 점검 (#198) - README·보고서 링크, run.ps1 인코딩, 보고서 변환 도우미."""

import re
from pathlib import Path

import pytest

from tools.build_report import _page_text, slugify_github, to_absolute_links

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "docs/final/report.md", "docs/final/business.md"]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)")


def anchors(path: Path) -> set[str]:
    """GitHub 이 만드는 머리글 앵커. 코드 블록 안의 # 은 머리글이 아니다."""
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    found = set()
    for title in re.findall(r"^#{1,6} (.+)$", text, flags=re.MULTILINE):
        title = re.sub(r"[`*]", "", title)
        found.add(slugify_github(title))
    return found


@pytest.mark.parametrize("doc", DOCS)
def test_relative_links_exist(doc):
    path = ROOT / doc
    for groups in LINK.findall(path.read_text(encoding="utf-8")):
        href = groups[0] or groups[1]
        if href.startswith(("http://", "https://", "mailto:")):
            continue
        target, _, fragment = href.partition("#")
        resolved = (path.parent / target).resolve() if target else path
        assert resolved.exists(), f"{doc}: 없는 경로 {href}"
        if fragment and resolved.suffix == ".md":
            assert fragment.lower() in anchors(resolved), f"{doc}: 없는 앵커 {href}"


def test_readme_has_no_control_chars():
    # 백슬래시 경로가 이스케이프로 깨지면 명령이 조용히 틀린다 (실제로 겪었다).
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert not [c for c in text if ord(c) < 32 and c not in "
	"]


def test_run_ps1_has_bom():
    # BOM 이 없으면 Windows PowerShell 5.1 이 한글을 ANSI 로 읽어 구문까지 깨진다.
    assert (ROOT / "run.ps1").read_bytes().startswith(b"\xef\xbb\xbf")


def test_run_ps1_binds_api_to_ipv4_loopback():
    # localhost 는 Windows 에서 IPv6 부터 시도해 첫 연결이 늦다 (docs/reports/search_latency.md).
    text = (ROOT / "run.ps1").read_text(encoding="utf-8-sig")
    assert "--host 127.0.0.1" in text


@pytest.mark.parametrize(
    ("title", "anchor"),
    [
        ("1. 배경과 문제 정의", "1-배경과-문제-정의"),
        ("부록. 문서와 코드 링크", "부록-문서와-코드-링크"),
        ("3.2 경쟁과 차별점", "32-경쟁과-차별점"),
        ("📎 제출 자료", "-제출-자료"),
    ],
)
def test_slugify_matches_github(title, anchor):
    assert slugify_github(title) == anchor


def test_absolute_links_keep_external_and_fragment():
    html = (
        '<a href="https://x.y/z">a</a> <a href="#요약">b</a> <a href="../adr/README.md#결정">c</a>'
    )
    out = to_absolute_links(html)
    assert 'href="https://x.y/z"' in out
    assert 'href="#요약"' in out
    assert (
        'href="https://github.com/Deleted-Code-Search/Deleted-Code-Search/blob/main/'
        'docs/adr/README.md#결정"' in out
    )


def test_page_text_drops_page_number_line():
    assert _page_text("12\n1. 배경과 문제") == "1.배경과문제"
    assert _page_text("표지 글\n둘째 줄") == "표지글둘째줄"
