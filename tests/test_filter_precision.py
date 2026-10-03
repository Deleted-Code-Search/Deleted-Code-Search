"""필터 정밀도 집계 테스트 (이슈 #89). 기대값은 손으로 계산했다 (각 테스트 주석)."""

import json
from pathlib import Path

import pytest

from eval import filter_precision as fp

# (sample_id, 층 = filter_status, sj, jh, hs)
VOTES = [
    ("k1", "KEPT", 1, 1, 1),
    ("k2", "KEPT", 1, 1, 0),
    ("k3", "KEPT", 1, 0, 0),
    ("k4", "KEPT", 1, 1, 1),
    ("e1", "NOISE_MOVE", 0, 0, 0),
    ("e2", "NOISE_TRIVIAL", 1, 0, 0),
    ("e3", "NOISE_TRIVIAL", 1, 1, 0),
    ("e4", "NOISE_MOVE", 0, 0, 0),
]
POPULATION = {"KEPT": 1000, "NOISE_MOVE": 200, "NOISE_TRIVIAL": 3000}


def build(votes, population):
    key = {
        "seed": 1,
        "population": population,
        "items": [
            {"sample_id": sid, "record_id": f"r-{sid}", "stratum": st, "filter_status": st}
            for sid, st, *_ in votes
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
            for sid, _st, *v in votes
        ]
    return files, key


def unanimous(prefix, stratum, yes, total):
    """만장일치 표 `total`건 중 `yes`건이 예."""
    return [(f"{prefix}{i}", stratum, *([int(i < yes)] * 3)) for i in range(total)]


def test_metrics_match_hand_calculation():
    files, key = build(VOTES, POPULATION)
    report = fp.aggregate(files, key)
    # 다수결: k1 예 k2 예 k3 아니오 k4 예 / e1 아니오 e4 아니오 (이동) / e2 아니오 e3 예 (사소)
    assert report.confusion == {
        "KEPT": {"yes": 3, "no": 1},
        "NOISE_MOVE": {"yes": 0, "no": 2},
        "NOISE_TRIVIAL": {"yes": 1, "no": 1},
    }
    assert report.precision == pytest.approx(0.75)
    assert report.noise_move_error_rate == 0.0
    assert report.stratum_yes_rate["NOISE_TRIVIAL"] == pytest.approx(0.5)
    # 가중 재현율 = 1000·0.75 / (1000·0.75 + 200·0 + 3000·0.5) = 750 / 2250 = 1/3
    assert report.weighted_recall == pytest.approx(1 / 3)
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
    assert report.yes_by_judge == {"hs": 2, "jh": 4, "sj": 6}
    assert not report.passed


def test_weighted_recall_uses_each_stratum_population():
    """층마다 표본 크기와 모집단 크기가 다를 때의 가중 재현율.

    표본: KEPT 5건 중 예 4 (p=0.8), NOISE_MOVE 4건 중 예 1 (0.25), NOISE_TRIVIAL 10건 중 예 1 (0.1)
    모집단: 1000 / 400 / 2000
        재현율 = 1000·0.8 / (1000·0.8 + 400·0.25 + 2000·0.1) = 800 / 1100 = 8/11 ≈ 0.727
    틀린 계산 둘과 다르다:
        표본 비율       4 / (4 + 1 + 1) = 0.667
        제외를 한 층으로 800 / (800 + 2400·(2/14)) ≈ 0.700
    """
    votes = unanimous("k", "KEPT", 4, 5)
    votes += unanimous("m", "NOISE_MOVE", 1, 4)
    votes += unanimous("t", "NOISE_TRIVIAL", 1, 10)
    files, key = build(votes, {"KEPT": 1000, "NOISE_MOVE": 400, "NOISE_TRIVIAL": 2000})
    report = fp.aggregate(files, key)
    assert report.sample_counts == {"KEPT": 5, "NOISE_MOVE": 4, "NOISE_TRIVIAL": 10}
    assert report.weighted_recall == pytest.approx(8 / 11)
    # 이동 층 단독 오판율 = 이동으로 제외됐는데 예 = 1/4
    assert report.noise_move_error_rate == pytest.approx(0.25)
    low, high = report.noise_move_error_ci95
    assert low < 0.25 < high
    assert report.fleiss_kappa == pytest.approx(1.0)
    assert not report.passed  # 정밀도 0.8


def test_weighted_recall_function_directly():
    population = {"KEPT": 10, "NOISE_MOVE": 0, "NOISE_TRIVIAL": 30}
    rates = {"KEPT": 0.5, "NOISE_MOVE": 1.0, "NOISE_TRIVIAL": 0.1}
    # 5 / (5 + 0 + 3) = 0.625
    assert fp.weighted_recall(population, rates) == pytest.approx(0.625)
    assert fp.weighted_recall(population, dict.fromkeys(rates, 0.0)) is None


def test_boundary_passes_at_exactly_ninety_percent():
    votes = unanimous("k", "KEPT", 9, 10)
    votes += unanimous("m", "NOISE_MOVE", 0, 5)
    votes += unanimous("t", "NOISE_TRIVIAL", 0, 5)
    files, key = build(votes, {"KEPT": 10, "NOISE_MOVE": 10, "NOISE_TRIVIAL": 10})
    report = fp.aggregate(files, key)
    assert report.precision == pytest.approx(0.9)
    assert report.fleiss_kappa == pytest.approx(1.0)  # 전원 만장일치
    assert report.weighted_recall == pytest.approx(1.0)  # 제외 중 예 0건
    assert report.passed


def test_fleiss_undefined_is_not_a_pass():
    votes = unanimous("k", "KEPT", 3, 3)
    votes += unanimous("m", "NOISE_MOVE", 2, 2)
    votes += unanimous("t", "NOISE_TRIVIAL", 2, 2)
    files, key = build(votes, POPULATION)
    report = fp.aggregate(files, key)
    assert report.precision == 1.0 and report.fleiss_kappa is None
    assert not report.passed


def test_stops_when_judgments_incomplete():
    files, key = build(VOTES, POPULATION)
    first = next(iter(files))
    files[first] = files[first][:-1]
    with pytest.raises(fp.AggregateError, match="판정하지 않았다"):
        fp.aggregate(files, key)


def test_stops_on_two_judges_or_record_mismatch():
    files, key = build(VOTES, POPULATION)
    two = dict(list(files.items())[:2])
    with pytest.raises(fp.AggregateError, match="3명"):
        fp.aggregate(two, key)
    files[Path("sj.jsonl")][0]["record_id"] = "other"
    with pytest.raises(fp.AggregateError, match="record_id"):
        fp.aggregate(files, key)


def test_stops_without_stratum_population():
    files, key = build(VOTES, {"KEPT": 1000, "EXCLUDED": 3200})
    with pytest.raises(fp.AggregateError, match="population.NOISE_MOVE"):
        fp.aggregate(files, key)


def test_main_writes_reports_and_exit_code(tmp_path):
    files, key = build(VOTES, POPULATION)
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
    assert payload["weighted_recall"] == pytest.approx(1 / 3)
    assert payload["noise_move_error_rate"] == 0.0
    assert "items" not in payload["key"]
    report = md_out.read_text(encoding="utf-8")
    assert "미달" in report
    assert "NOISE_MOVE 오판율" in report and "0/2" in report


# ── 2차 재측정: 4층 (NOISE_FORMAT 추가). 층은 key.json 의 sample_sizes 에서 읽는다 ──


def build_v2(votes, population):
    files, key = build(votes, population)
    key["round"] = 2
    key["sample_sizes"] = {"KEPT": 100, "NOISE_MOVE": 34, "NOISE_TRIVIAL": 33, "NOISE_FORMAT": 33}
    return files, key


def four_strata_votes():
    votes = unanimous("k", "KEPT", 9, 10)
    votes += unanimous("m", "NOISE_MOVE", 1, 5)
    votes += unanimous("t", "NOISE_TRIVIAL", 1, 4)
    votes += unanimous("f", "NOISE_FORMAT", 2, 5)
    return votes


V2_POPULATION = {"KEPT": 1000, "NOISE_MOVE": 500, "NOISE_TRIVIAL": 2000, "NOISE_FORMAT": 100}


def test_four_strata_weighted_recall_hand_calculation():
    """표본 p: KEPT 9/10=0.9, MOVE 1/5=0.2, TRIVIAL 1/4=0.25, FORMAT 2/5=0.4
    모집단 N: 1000 / 500 / 2000 / 100
        재현율 = 1000·0.9 / (1000·0.9 + 500·0.2 + 2000·0.25 + 100·0.4)
               = 900 / (900 + 100 + 500 + 40) = 900 / 1540 = 45/77 ≈ 0.584
    NOISE_FORMAT 층을 빼고 3층으로 내면 900 / 1500 = 0.6 — 그 값과 다르다.
    """
    files, key = build_v2(four_strata_votes(), V2_POPULATION)
    report = fp.aggregate(files, key)
    assert report.sample_counts == {
        "KEPT": 10,
        "NOISE_MOVE": 5,
        "NOISE_TRIVIAL": 4,
        "NOISE_FORMAT": 5,
    }
    assert report.weighted_recall == pytest.approx(45 / 77)
    assert report.weighted_recall != pytest.approx(0.6)
    assert report.population == V2_POPULATION
    assert report.precision == pytest.approx(0.9)
    assert report.noise_move_error_rate == pytest.approx(0.2)
    # 포맷으로 제외됐는데 예 = 2/5, 구간과 함께 따로 보고
    assert report.noise_format_error_rate == pytest.approx(0.4)
    low, high = report.noise_format_error_ci95
    assert low < 0.4 < high
    assert report.confusion["NOISE_FORMAT"] == {"yes": 2, "no": 3}
    assert report.fleiss_kappa == pytest.approx(1.0)
    assert report.passed  # 목표는 1차와 같다: 정밀도 ≥ 0.9, Fleiss ≥ 0.7


def test_four_strata_report_shows_format_error_rate(tmp_path):
    files, key = build_v2(four_strata_votes(), V2_POPULATION)
    report = fp.aggregate(files, key)
    text = fp.format_report(report, key)
    assert "2차 재측정" in text
    assert "NOISE_FORMAT 오판율" in text and "2/5" in text
    assert "NOISE_TRIVIAL 중 예 비율 | 25.0%" in text
    assert "| NOISE_FORMAT | 2 | 3 |" in text


def test_four_strata_key_requires_format_population():
    files, key = build_v2(four_strata_votes(), {"KEPT": 1, "NOISE_MOVE": 1, "NOISE_TRIVIAL": 1})
    with pytest.raises(fp.AggregateError, match="population.NOISE_FORMAT"):
        fp.aggregate(files, key)


def test_round_one_key_has_no_format_rate():
    """1차 키(3층)는 그대로 읽힌다 — NOISE_FORMAT 오판율은 None, 보고서에 줄이 없다."""
    files, key = build(VOTES, POPULATION)
    key["sample_sizes"] = {"KEPT": 100, "NOISE_MOVE": 50, "NOISE_TRIVIAL": 50}
    report = fp.aggregate(files, key)
    assert report.noise_format_error_rate is None and report.noise_format_error_ci95 is None
    assert report.weighted_recall == pytest.approx(1 / 3)
    text = fp.format_report(report, key)
    assert "NOISE_FORMAT" not in text and "#80 에서 보류한 항목" in text
