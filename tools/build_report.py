"""결과보고서 PDF 만들기 - docs/final/report.md → docs/final/report.pdf (#198). 담당: 성제

순수 파이썬으로 md → HTML 을 만들고, 그림·표·쪽 나눔은 CSS 로 잡아 Edge 헤드리스로 인쇄한다.
LaTeX·pandoc·네이티브 PDF 라이브러리를 쓰지 않는다 (스마트 앱 컨트롤, ADR-022 와 같은 이유).

    pip install -e ".[report]"
    python -m tools.build_report               # report.pdf
    python -m tools.build_report --keep-html   # 확인용 report.html 도 남긴다

쪽 번호는 두 번 인쇄해서 넣는다. 1차 PDF 에서 각 장이 몇 쪽인지 읽어 목차에 적고 다시 인쇄한다.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_MD = ROOT / "docs" / "final" / "report.md"
REPORT_PDF = ROOT / "docs" / "final" / "report.pdf"
GITHUB_BLOB = "https://github.com/Deleted-Code-Search/Deleted-Code-Search/blob/main/"
COVER_END = "<!-- cover-end -->"
TOC_HEADING_ID = "목차"

EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

CSS = """
@page {
  size: A4;
  margin: 18mm 17mm 16mm 17mm;
  @bottom-center { content: counter(page); font-size: 9pt; color: #5f5e58; }
}
@page :first { @bottom-center { content: ""; } }
html { font-size: 10pt; }
body {
  font-family: "Malgun Gothic", "맑은 고딕", sans-serif;
  line-height: 1.55; color: #1f1f1d; word-break: keep-all; overflow-wrap: break-word;
}
h1, h2, h3, h4 { line-height: 1.3; break-after: avoid; }
h2 {
  font-size: 18pt; break-before: page; margin: 0 0 14pt;
  padding-bottom: 6pt; border-bottom: 2px solid #2a78d6;
}
h3 { font-size: 13pt; margin: 18pt 0 6pt; color: #184f95; }
h4 { font-size: 11pt; margin: 12pt 0 4pt; }
p { margin: 0 0 7pt; }
ul, ol { margin: 0 0 8pt; padding-left: 18pt; }
li { margin-bottom: 3pt; }
a { color: #1c5cab; text-decoration: none; }
code {
  font-family: Consolas, "Malgun Gothic", monospace; font-size: 0.9em;
  background: #f3f2ee; padding: 0 2pt; border-radius: 2pt;
}
pre {
  background: #f3f2ee; padding: 8pt; font-size: 8.5pt; line-height: 1.4; white-space: pre-wrap;
}
pre code { background: none; padding: 0; }
blockquote {
  margin: 0 0 8pt; padding: 4pt 10pt; border-left: 3px solid #2a78d6; background: #f5f9fe;
}
blockquote p { margin: 0; }
table {
  border-collapse: collapse; width: 100%; margin: 4pt 0 10pt; font-size: 9pt; line-height: 1.42;
}
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { background: #e6f0fb; font-weight: 700; }
th, td { border: 1px solid #c9c8c0; padding: 3pt 5pt; vertical-align: top; }
td code { white-space: nowrap; }
td:has(> code ~ code) code { white-space: normal; }
img {
  display: block; max-width: 100%; max-height: 90mm; margin: 6pt auto 3pt;
  border: 1px solid #e4e3dd;
}
img.figure-svg { border: none; }
p:has(> img) { break-inside: avoid; break-after: avoid; margin-bottom: 2pt; }
.caption, p:has(> img) + p > em:only-child { font-size: 9pt; color: #5f5e58; }
.cover { height: 245mm; display: flex; flex-direction: column; justify-content: center; }
.cover h1 { font-size: 30pt; margin: 0 0 8pt; color: #184f95; }
.cover table { width: 80%; font-size: 11pt; margin-top: 28pt; }
.cover td:first-child { width: 18%; white-space: nowrap; }
.cover th, .cover td { padding: 5pt 8pt; }
.cover th { width: 26%; }
.toc-page { float: right; color: #5f5e58; }
"""


def slugify_github(value: str, separator: str = "-") -> str:
    """GitHub 머리글 앵커와 같은 규칙 - 그래야 report.md 안 링크가 GitHub 과 PDF 양쪽에서 맞는다."""
    value = value.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", separator)


def to_absolute_links(html: str) -> str:
    """저장소 안 링크를 GitHub 주소로. PDF 는 저장소 밖에서 열리므로 상대 경로가 깨진다."""

    def repl(match: re.Match[str]) -> str:
        href = match.group(1)
        if href.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        path = href.split("#")[0]
        fragment = href[len(path) :]
        resolved = (REPORT_MD.parent / path).resolve()
        return f'href="{GITHUB_BLOB}{resolved.relative_to(ROOT).as_posix()}{fragment}"'

    return re.sub(r'href="([^"]+)"', repl, html)


def render_html(md_text: str, pages: dict[str, int] | None = None) -> str:
    """md → 인쇄용 HTML. `pages` 는 머리글 id → 쪽 번호 (있으면 목차에 적는다)."""
    import markdown  # 보고서를 만들 때만 필요하다 (extras: report)

    cover, _, body = md_text.partition(COVER_END)
    convert = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        extension_configs={"toc": {"slugify": slugify_github}},
    )
    cover_html = convert.convert(cover)
    convert.reset()
    body_html = convert.convert(body)
    if pages:
        for anchor, page in pages.items():
            body_html = re.sub(
                rf'(<a href="#{re.escape(anchor)}">)(.*?)(</a>)(?=\s*</li>)',
                rf'<span class="toc-page">{page}</span>\1\2\3',
                body_html,
                count=1,
            )
    # 그림 경로를 파일 주소로 - HTML 을 임시 폴더에 두고 인쇄한다.
    base = REPORT_MD.parent.resolve()
    html = f"<section class='cover'>{cover_html}</section>\n{body_html}"
    html = to_absolute_links(html)

    def image(match: re.Match[str]) -> str:
        before, src = match.groups()
        extra = ' class="figure-svg"' if src.endswith(".svg") else ""
        return f'<img {before}src="{(base / src).resolve().as_uri()}"{extra}'

    html = re.sub(r'<img ([^>]*?)src="([^"]+)"', image, html)
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f"<title>지워진 코드 검색엔진 결과보고서</title><style>{CSS}</style></head>"
        f"<body>{html}</body></html>"
    )


def find_edge() -> str:
    found = shutil.which("msedge")
    candidates = ([found] if found else []) + list(EDGE_PATHS)
    for path in candidates:
        if os.path.exists(path):
            return path
    raise SystemExit("Microsoft Edge 를 찾지 못했다 (msedge.exe)")


def print_pdf(html: str, out: Path, edge: str, timeout: int = 180) -> None:
    out.unlink(missing_ok=True)
    # Edge 자식 프로세스가 프로필을 조금 더 잡고 있어 임시 폴더 지우기 실패는 넘긴다.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        page = Path(tmp) / "report.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run(
            [
                edge,
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--virtual-time-budget=10000",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                f"--print-to-pdf={out}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        # msedge.exe 는 자식에게 넘기고 먼저 끝날 수 있다. 파일 크기가 멈출 때까지 기다린다.
        deadline = time.monotonic() + timeout
        last_size = -1
        while time.monotonic() < deadline:
            size = out.stat().st_size if out.exists() else -1
            if size > 0 and size == last_size:
                return
            last_size = size
            time.sleep(1)
    raise SystemExit(f"PDF 가 만들어지지 않았다: {out}")


def _page_text(text: str) -> str:
    """공백을 없앤 쪽 글. 쪽 번호(첫 줄)는 뺀다 - 안 빼면 머리글 "1. 배경" 앞에 번호가 붙는다."""
    first, _, rest = text.partition(chr(10))
    return re.sub(r"\s+", "", rest if first.strip().isdigit() else text)


def chapter_pages(pdf: Path, md_text: str) -> dict[str, int]:
    """PDF 에서 각 `##` 머리글이 처음 나오는 쪽을 찾는다. 목차 쪽 뒤에서부터 찾는다."""
    from pypdf import PdfReader

    texts = [_page_text(page.extract_text() or "") for page in PdfReader(pdf).pages]
    toc_page = next(i for i, text in enumerate(texts) if text.startswith(TOC_HEADING_ID))
    pages: dict[str, int] = {}
    for title in re.findall(r"^## (.+)$", md_text, flags=re.MULTILINE):
        if title == TOC_HEADING_ID:
            continue
        needle = re.sub(r"\s+", "", title)
        start = 0 if title == "요약" else toc_page + 1
        for index in range(start, len(texts)):
            if texts[index].startswith(needle):
                pages[slugify_github(title)] = index + 1
                break
        else:
            raise SystemExit(f"PDF 에서 장을 찾지 못했다: {title}")
    return pages


def build(keep_html: bool = False) -> int:
    """PDF 를 만들고 전체 쪽 수를 돌려준다."""
    from pypdf import PdfReader

    md_text = REPORT_MD.read_text(encoding="utf-8")
    edge = find_edge()
    print_pdf(render_html(md_text), REPORT_PDF, edge)
    pages = chapter_pages(REPORT_PDF, md_text)
    html = render_html(md_text, pages)
    print_pdf(html, REPORT_PDF, edge)
    if keep_html:
        REPORT_PDF.with_suffix(".html").write_text(html, encoding="utf-8")
    return len(PdfReader(REPORT_PDF).pages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-html", action="store_true", help="확인용 report.html 을 남긴다")
    args = parser.parse_args(argv)
    total = build(keep_html=args.keep_html)
    print(f"{REPORT_PDF.relative_to(ROOT)}: {total}쪽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
