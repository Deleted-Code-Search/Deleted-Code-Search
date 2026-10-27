"""100개 적용 요약 테스트 (#180) - 분포와 표본 일치율."""

import json

import pytest

from classify import apply_report


def write(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def row(rid, label, version, repo="a/b", grade="INFERRED"):
    return {
        "record_id": rid,
        "repo": repo,
        "label": label,
        "evidence_grade": grade,
        "classifier_version": version,
    }


def test_agreement_on_the_sample_by_hand(tmp_path):
    """표본 3건 중 2건 일치. 갈린 쌍은 (모델만, LLM 포함) 순서로 센다."""
    model = write(
        tmp_path / "m.jsonl",
        [
            row("x1", "DEAD", "r1+m2"),
            row("x2", "UNK", "r1+m2", repo="c/d"),
            row("x3", "DESIGN", "r1+m2"),
            row("x4", "FEAT", "r1+m2"),  # 표본 밖
        ],
    )
    llm = write(
        tmp_path / "l.jsonl",
        [
            row("x1", "DEAD", "r1+m2+c3+llm:m"),
            row("x2", "UNK", "r1+m2+c3+llm:m", repo="c/d"),
            row("x3", "FEAT", "r1+m2+c3+llm:m"),
        ],
    )

    result = apply_report.compare(model, llm)

    assert (result["n"], result["agree"]) == (3, 2)
    assert result["per_repo"]["a/b"] == {"n": 2, "agree": 1, "agreement": 0.5}
    assert result["disagreements_top"] == [{"model_only": "DESIGN", "with_llm": "FEAT", "count": 1}]


def test_a_sample_record_missing_from_the_model_only_output_is_refused(tmp_path):
    """같은 대상 범위가 아니면 일치율이 무엇을 재는지 모른다."""
    model = write(tmp_path / "m.jsonl", [row("x1", "DEAD", "r1+m2")])
    llm = write(tmp_path / "l.jsonl", [row("x1", "DEAD", "v"), row("x9", "UNK", "v")])

    with pytest.raises(ValueError, match="모델만 출력에 없는 표본 1건"):
        apply_report.compare(model, llm)


def test_distribution_counts_versions_labels_and_human_overlap(tmp_path):
    path = write(
        tmp_path / "m.jsonl",
        [row("x1", "DEAD", "r1+m2"), row("x2", "DEAD", "r1+m2", grade="UNKNOWN")],
    )

    result = apply_report.distribution(path, labeled={"x2"})

    assert result["n"] == 2
    assert result["versions"] == {"r1+m2": 2}
    assert result["grades"] == {"INFERRED": 1, "UNKNOWN": 1}
    assert result["human_labeled_overlap"] == 1
