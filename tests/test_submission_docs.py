"""제출 문서 점검 (#198, #200) - README·보고서 링크와 앵커, run.ps1 인코딩."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "docs/final/report.md"]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)")


def slugify_github(value: str, separator: str = "-") -> str:
    """GitHub 머리글 앵커와 같은 규칙 - README 의 report.md 절 링크를 검사하는 데 쓴다."""
    value = value.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", separator)


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
    allowed = {chr(9), chr(10), chr(13)}  # 탭·줄바꿈
    assert not [c for c in text if ord(c) < 32 and c not in allowed]


def test_run_ps1_has_bom():
    # BOM 이 없으면 Windows PowerShell 5.1 이 한글을 ANSI 로 읽어 구문까지 깨진다.
    assert (ROOT / "run.ps1").read_bytes().startswith(b"\xef\xbb\xbf")


def test_run_cmd_launches_script_beside_it():
    raw = (ROOT / "run.cmd").read_bytes()
    assert raw.count(bytes([13, 10])) == raw.count(bytes([10]))  # cmd.exe 는 CRLF
    text = raw.decode("ascii")
    assert '-ExecutionPolicy Bypass -File "%~dp0run.ps1"' in text


def test_run_ps1_binds_api_to_ipv4_loopback():
    # localhost 는 Windows 에서 IPv6 부터 시도해 첫 연결이 늦다 (docs/reports/search_latency.md).
    text = (ROOT / "run.ps1").read_text(encoding="utf-8-sig")
    assert "--host 127.0.0.1" in text


@pytest.mark.parametrize(
    ("title", "anchor"),
    [
        ("1. 배경과 문제 정의", "1-배경과-문제-정의"),
        ("부록. 실행 방법과 코드", "부록-실행-방법과-코드"),
        ("7.2 경쟁과 차별점", "72-경쟁과-차별점"),
        ("📎 제출 자료", "-제출-자료"),
    ],
)
def test_slugify_matches_github(title, anchor):
    assert slugify_github(title) == anchor
