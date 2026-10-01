"""필터 정밀도 집계 테스트 (이슈 #89). 기대값은 손으로 계산했다 (각 테스트 주석)."""

import json
from pathlib import Path

import pytest

from eval import filter_precision as fp

# (sample_id, stratum, filter_status, sj, jh, hs)
VOTES = [
    ("k1", "KEPT", "KEPT", 1, 1, 1),
    ("k2", "KEPT", "KEPT", 1, 1, 0),
    ("k3", "KEPT", "KEPT", 1, 0, 0),
    ("k4", "KEPT", "KEPT", 1, 1, 1),
    ("e1", "EXCLUDED", "NOISE_MOVE", 0, 0, 0),
    ("e2", "EXCLUDED", "NOISE_TRIVIAL", 1, 0, 0),
    ("e3", "EXCLUDED", "NOISE_TRIVIAL", 1, 1, 0),
    ("e4", "EXCLUDED", "NOISE_MOVE", 0, 0, 0),
]


def build(votes, population):
    key = {
        "seed": 1,
        "population": population,
        "items": [
            {"sample_id": sid, "record_id": f"r-{sid}", "stratum": st, "filter_status": fs}
            for sid, st, fs, *_ in votes
        ],
    }
    files = {}
    for column, judge in enumerate(("sj", "jh", "hs")):
        files[Path(f"{judge}.jsonl")] = [
            {
                "sample_id": sid,
                "record_id": f"r-{sid}",
                "judge": judge,
                "meaningful": bool(v[column]),
            }
            for sid, _st, _fs, *v in votes
        ]
    return files, key


def test_metrics_match_hand_calculation():
    files, key = build(VOTES, {"KEPT": 1000, "EXCLUDED": 3000})
    report = fp.aggregate(files, key)
    # 다수결: k1 예 k2 예 k3 아니오 k4 예 / e1 아니오 e2 아니오 e3 예 e4 아니오
    assert report.confusion == {"KEPT": {"yes": 3, "no": 1}, "EXCLUDED": {"yes": 1, "no": 3}}
    assert report.precision == pytest.approx(0.75)
    assert report.excluded_yes_rate == pytest.approx(0.25)
    # 가중 재현율 = 1000·0.75 / (1000·0.75 + 3000·0.25) = 750 / 1500 = 0.5
    # (표본 비율로 내면 3/4 = 0.75 — 틀린 값)
    assert report.weighted_recall == pytest.approx(0.5)
    # Fleiss: 예 표 수 [3,2,1,3,0,1,2,0], P_i = 1 또는 1/3 → P̄ = (4 + 4/3)/8 = 2/3
    #         p_예 = 12/24 = 0.5 → P_e = 0.5 → κ = (2/3 − 1/2)/(1/2) = 1/3
    assert report.fleiss_kappa == pytest.approx(1 / 3)
    # Cohen sj+jh: p_o 6/8, p_e .75·.5 + .25·.5 = .5 → .5
    #       hs+sj: p_o 4/8, p_e .75·.25 + .25·.75 = .375 → .2
    #       hs+jh: p_o 6/8, p_e .5·.25 + .5·.75 = .5 → .5
    assert report.pairwise_kappa == {
        "hs+jh": pytest.approx(0.5),
        "hs+sj": pytest.approx(0.2),
        "jh+sj": pytest.approx(0.5),
    }
    assert report.unanimous == 4
    assert report.by_filter_status == {
        "KEPT": {"yes": 3, "no": 1},
        "NOISE_MOVE": {"yes": 0, "no": 2},
        "NOISE_TRIVIAL": {"yes": 1, "no": 1},
    }
    assert report.yes_by_judge == {"hs": 2, "jh": 4, "sj": 6}
    assert not report.passed


def test_boundary_passes_at_exactly_ninety_percent():
    votes = [(f"k{i}", "KEPT", "KEPT", *([1] * 3 if i < 9 else [0] * 3)) for i in range(10)]
    votes += [(f"e{i}", "EXCLUDED", "NOISE_MOVE", 0, 0, 0) for i in range(10)]
    files, key = build(votes, {"KEPT": 10, "EXCLUDED": 10})
    report = fp.aggregate(files, key)
    assert report.precision == pytest.approx(0.9)
    assert report.fleiss_kappa == pytest.approx(1.0)  # 전원 만장일치
    assert report.weighted_recall == pytest.approx(1.0)  # 제외 중 예 0건
    assert report.passed


def test_fleiss_undefined_is_not_a_pass():
    votes = [(f"k{i}", "KEPT", "KEPT", 1, 1, 1) for i in range(3)]
    votes += [(f"e{i}", "EXCLUDED", "NOISE_MOVE", 1, 1, 1) for i in range(3)]
    files, key = build(votes, {"KEPT": 10, "EXCLUDED": 10})
    report = fp.aggregate(files, key)
    assert report.precision == 1.0 and report.fleiss_kappa is None
    assert not report.passed


def test_stops_when_judgments_incomplete():
    files, key = build(VOTES, {"KEPT": 10, "EXCLUDED": 10})
    first = next(iter(files))
    files[first] = files[first][:-1]
    with pytest.raises(fp.AggregateError, match="판정하지 않았다"):
        fp.aggregate(files, key)


def test_stops_on_two_judges_or_record_mismatch():
    files, key = build(VOTES, {"KEPT": 10, "EXCLUDED": 10})
    two = dict(list(files.items())[:2])
    with pytest.raises(fp.AggregateError, match="3명"):
        fp.aggregate(two, key)
    files[Path("sj.jsonl")][0]["record_id"] = "other"
    with pytest.raises(fp.AggregateError, match="record_id"):
        fp.aggregate(files, key)


def test_main_writes_reports_and_exit_code(tmp_path):
    files, key = build(VOTES, {"KEPT": 1000, "EXCLUDED": 3000})
    paths = []
    for path, rows in files.items():
        target = tmp_path / path.name
        target.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        paths.append(str(target))
    key_path = tmp_path / "key.json"
    key_path.write_text(json.dumps(key), encoding="utf-8")
    json_out, md_out = tmp_path / "fp.json", tmp_path / "fp.md"
    code = fp.main(
        [*paths, "--key", str(key_path), "--json-out", str(json_out), "--md-out", str(md_out)]
    )
    assert code == 3  # 미달
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["weighted_recall"] == pytest.approx(0.5)
    assert "items" not in payload["key"]
    assert "미달" in md_out.read_text(encoding="utf-8")
