"""검색 응답 시간 측정 (#191, CHARTER §10.5 "검색 응답 ≤ 1초"). 담당: 성제

질의 20개(키워드만 / 키워드+필터 / 필터만)를 `GET /api/search` 로 보내 응답 시간 p50·p95 를 낸다.
질의마다 한 번 먼저 보내고(첫 호출, 따로 적는다) 그 뒤 `--repeat` 번을 잰다.

기본은 같은 프로세스의 FastAPI TestClient 로 부른다 - API 처리 + SQLite 시간이고 네트워크는 없다.
`--base-url http://localhost:8000` 을 주면 떠 있는 서버에 HTTP 로 보낸다.

실행:
    python -m eval.search_latency
    python -m eval.search_latency --base-url http://localhost:8000 --repeat 10
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "search_latency/1"
TARGET_MS = 1000.0
DEFAULT_OUT = Path("docs") / "reports" / "search_latency.json"

# (유형, 파라미터). 유형: keyword / keyword+filter / filter
QUERIES: list[tuple[str, dict[str, Any]]] = [
    # 키워드만 - 흔한 단어(self, test)는 수십만 건이 맞는 최악 쪽이다
    ("keyword", {"q": "retry backoff"}),
    ("keyword", {"q": "py2"}),
    ("keyword", {"q": "deprecated"}),
    ("keyword", {"q": "memory leak"}),
    ("keyword", {"q": "thread safe"}),
    ("keyword", {"q": "cache"}),
    ("keyword", {"q": "self"}),
    ("keyword", {"q": "test"}),
    ("keyword", {"q": "get_config*"}),
    # 키워드 + 필터
    ("keyword+filter", {"q": "unused", "source": "HUMAN"}),
    ("keyword+filter", {"q": "replace", "source": "HUMAN", "grade": "EXPLICIT"}),
    ("keyword+filter", {"q": "deprecated", "grade": "EXPLICIT"}),
    ("keyword+filter", {"q": "migration", "repo": "django/django"}),
    ("keyword+filter", {"q": "timeout", "kind": "FULL_FUNCTION", "year_from": 2022}),
    ("keyword+filter", {"q": "self", "repo": "home-assistant/core", "year_to": 2018}),
    # 필터만
    ("filter", {"source": "HUMAN"}),
    ("filter", {"reason": "SEC"}),
    ("filter", {"repo": "apache/airflow", "kind": "PARTIAL"}),
    ("filter", {"year_from": 2025, "grade": "EXPLICIT"}),
    ("filter", {"source": "UNDISTINGUISHED", "year_to": 2015, "offset": 200}),
]


def percentile(values: Sequence[float], p: float) -> float:
    """선형 보간 백분위 (numpy 기본과 같다)."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def make_caller(base_url: str | None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    if base_url:

        def call_http(params: dict[str, Any]) -> dict[str, Any]:
            url = f"{base_url.rstrip('/')}/api/search?{urllib.parse.urlencode(params)}"
            with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - 사용자가 준 주소
                return json.loads(response.read())

        return call_http

    from fastapi.testclient import TestClient

    from search import api

    client = TestClient(api.app)

    def call_local(params: dict[str, Any]) -> dict[str, Any]:
        response = client.get("/api/search", params=params)
        response.raise_for_status()
        return response.json()

    return call_local


def measure(call: Callable[[dict[str, Any]], dict[str, Any]], repeat: int) -> list[dict[str, Any]]:
    rows = []
    for kind, params in QUERIES:
        started = time.perf_counter()
        body = call(params)
        first_ms = (time.perf_counter() - started) * 1000
        samples = []
        for _ in range(repeat):
            started = time.perf_counter()
            call(params)
            samples.append((time.perf_counter() - started) * 1000)
        rows.append(
            {
                "type": kind,
                "params": params,
                "total": body["total"],
                "total_capped": body["total_capped"],
                "first_ms": round(first_ms, 1),
                "median_ms": round(statistics.median(samples), 1),
                "max_ms": round(max(samples), 1),
                "samples_ms": [round(s, 1) for s in samples],
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for row in rows:
        groups.setdefault(row["type"], []).extend(row["samples_ms"])
        groups.setdefault("all", []).extend(row["samples_ms"])
    firsts = [row["first_ms"] for row in rows]
    summary = {
        name: {
            "n": len(values),
            "p50_ms": round(percentile(values, 50), 1),
            "p95_ms": round(percentile(values, 95), 1),
            "max_ms": round(max(values), 1),
        }
        for name, values in groups.items()
    }
    summary["first_call"] = {
        "n": len(firsts),
        "p50_ms": round(percentile(firsts, 50), 1),
        "p95_ms": round(percentile(firsts, 95), 1),
        "max_ms": round(max(firsts), 1),
    }
    return summary


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# 검색 응답 시간 (#191)",
        "",
        f"CHARTER §10.5 목표 **≤ {TARGET_MS:.0f}ms**. `python -m eval.search_latency` 출력이다.",
        "",
        f"- 색인: `{report['index']}` ({report['index_bytes'] / 1e9:.2f} GB)",
        f"- 호출: {report['mode']} · 질의 {len(report['queries'])}개 × {report['repeat']}회"
        " (질의마다 첫 호출 1회는 따로)",
        f"- 환경: {report['environment']}",
        "",
        "| 유형 | n | p50 | p95 | 최대 | ≤ 1초 |",
        "|---|---:|---:|---:|---:|:---:|",
    ]
    for name in ("keyword", "keyword+filter", "filter", "all", "first_call"):
        if name not in s:
            continue
        g = s[name]
        ok = "O" if g["p95_ms"] <= TARGET_MS else "**X**"
        lines.append(
            f"| {name} | {g['n']} | {g['p50_ms']:.0f}ms | {g['p95_ms']:.0f}ms "
            f"| {g['max_ms']:.0f}ms | {ok} |"
        )
    lines += ["", "## 질의별", "", "| 유형 | 파라미터 | 건수 | 첫 호출 | 중앙값 | 최대 |"]
    lines.append("|---|---|---:|---:|---:|---:|")
    for row in report["queries"]:
        params = ", ".join(f"{k}={v}" for k, v in row["params"].items())
        total = f"{row['total']:,}{'+' if row['total_capped'] else ''}"
        lines.append(
            f"| {row['type']} | `{params}` | {total} | {row['first_ms']:.0f}ms "
            f"| {row['median_ms']:.0f}ms | {row['max_ms']:.0f}ms |"
        )
    lines += ["", "## 해석", "", report.get("notes") or "(없음)", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="검색 응답 시간 p50·p95 (#191)")
    parser.add_argument("--base-url", help="떠 있는 API 주소. 없으면 TestClient")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--notes", help="md 의 해석 칸에 넣을 문장")
    args = parser.parse_args(argv)

    from search import api

    index = api.index_path()
    if not index.is_file():
        print(f"오류: 색인 {index} 가 없다. python -m search.index 먼저", file=sys.stderr)
        return 1
    rows = measure(make_caller(args.base_url), args.repeat)
    report = {
        "schema": SCHEMA_VERSION,
        "target_ms": TARGET_MS,
        "index": index.as_posix(),
        "index_bytes": index.stat().st_size,
        "mode": f"HTTP {args.base_url}" if args.base_url else "TestClient (같은 프로세스)",
        "repeat": args.repeat,
        "environment": f"{platform.system()} {platform.release()}, Python "
        f"{platform.python_version()}, SQLite {api.sqlite3.sqlite_version}",
        "summary": summarize(rows),
        "over_target": [r for r in rows if r["median_ms"] > TARGET_MS],
        "queries": rows,
        "notes": args.notes,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(args.out.with_suffix(".md"), report)
    s = report["summary"]["all"]
    print(f"전체 p50 {s['p50_ms']:.0f}ms · p95 {s['p95_ms']:.0f}ms → {args.out}")
    for row in report["over_target"]:
        print(f"  1초 초과: {row['type']} {row['params']} 중앙값 {row['median_ms']:.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
