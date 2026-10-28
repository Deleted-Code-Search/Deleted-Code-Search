"""100개 적용 요약 테스트 (#180) - 분포와 표본 일치율, 학습/신규 저장소 그룹."""

import json

import pytest

from classify import apply_report


def write(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def row(rid, label, version, repo="a/b", grade="INFERRED", training=True):
    return {
        "record_id": rid,
        "repo": repo,
        "training_repo": training,
        "label": label,
        "evidence_grade": grade,
        "classifier_version": version,
    }


def test_agreement_on_the_sample_by_hand(tmp_path):
    """표본 3건 중 2건 일치. 갈린 쌍은 (모델만, LLM 포함) 순서로 센다. 파일 여러 개를 잇는다."""
    model = [
        write(
            tmp_path / "m1.jsonl",
            [row("x1", "DEAD", "r1+m2"), row("x2", "UNK", "r1+m2", repo="c/d")],
        ),
        write(
            tmp_path / "m2.jsonl",
            [row("x3", "DESIGN", "r1+m2"), row("x4", "FEAT", "r1+m2")],  # x4 는 표본 밖
        ),
    ]
    llm = write(
        tmp_path / "l.jsonl",
        [
            row("x1", "DEAD", "r1+m2+c3+llm:m"),
            row("x2", "UNK", "r1+m2+c3+llm:m", repo="c/d"),
            row("x3", "FEAT", "r1+m2+c3+llm:m"),
        ],
    )

    result = apply_report.compare(model, [llm])

    assert (result["n"], result["agree"]) == (3, 2)
    assert result["per_repo"]["a/b"] == {"n": 2, "agree": 1, "agreement": 0.5}
    assert result["disagreements_top"] == [{"model_only": "DESIGN", "with_llm": "FEAT", "count": 1}]


def test_agreement_is_split_by_repo_group_with_kappa_by_hand(tmp_path):
    """학습 저장소 2건, 신규 저장소 3건. 그룹은 모델만 출력의 플래그로 나눈다.

    신규: (UNK,UNK) (UNK,BUG) (DESIGN,DESIGN) - 2/3 일치, LLM 이 UNK·DESIGN 아닌 건 0/1.
        kappa: 관측 2/3, 우연 (UNK 2x1 + DESIGN 1x1)/9 = 1/3 -> (2/3-1/3)/(2/3) = 0.5
    학습: (DEAD,DEAD) (DESIGN,FEAT) - 1/2, UNK·DESIGN 아닌 건 1/2.
        kappa: 관측 1/2, 우연 (DEAD 1x1)/4 = 1/4 -> (1/4)/(3/4) = 0.3333
    """
    model = write(
        tmp_path / "m.jsonl",
        [
            row("x1", "DEAD", "v"),
            row("x3", "DESIGN", "v"),
            row("x2", "UNK", "v", training=False),
            row("x5", "UNK", "v", training=False),
            row("x6", "DESIGN", "v", training=False),
        ],
    )
    # LLM 쪽 플래그는 일부러 반대로 둔다 - 그룹은 모델만 쪽 플래그를 따른다.
    llm = write(
        tmp_path / "l.jsonl",
        [
            row("x1", "DEAD", "w", training=False),
            row("x3", "FEAT", "w", training=False),
            row("x2", "UNK", "w"),
            row("x5", "BUG", "w"),
            row("x6", "DESIGN", "w"),
        ],
    )

    groups = apply_report.compare([model], [llm])["by_repo_group"]

    new, training = groups["new_repos"], groups["training_repos"]
    assert (new["n"], new["agree"], new["kappa"]) == (3, 2, 0.5)
    assert new["outside_unclear"] == {"n": 1, "agree": 0, "agreement": 0.0}
    assert new["by_llm_label"] == {
        "UNK": {"n": 1, "agree": 1},
        "BUG": {"n": 1, "agree": 0},
        "DESIGN": {"n": 1, "agree": 1},
    }
    assert (training["n"], training["agree"], training["kappa"]) == (2, 1, 0.3333)
    assert training["outside_unclear"] == {"n": 2, "agree": 1, "agreement": 0.5}


def test_kappa_is_zero_when_one_side_always_says_the_same():
    """모델만이 늘 UNK 면 맞은 건 모두 우연이다."""
    pairs = apply_report.Counter({("UNK", "UNK"): 3, ("UNK", "DEAD"): 1})

    assert apply_report.agreement(pairs)["kappa"] == 0.0


def test_a_sample_record_missing_from_the_model_only_output_is_refused(tmp_path):
    """같은 대상 범위가 아니면 일치율이 무엇을 재는지 모른다."""
    model = write(tmp_path / "m.jsonl", [row("x1", "DEAD", "r1+m2")])
    llm = write(tmp_path / "l.jsonl", [row("x1", "DEAD", "v"), row("x9", "UNK", "v")])

    with pytest.raises(ValueError, match="모델만 출력에 없는 표본 1건"):
        apply_report.compare([model], [llm])


def test_a_duplicate_record_id_is_refused_in_both_inputs(tmp_path):
    """같은 파일을 두 번 넘기면 분포는 두 번 세고 일치율은 덮어쓴다 - 분모가 갈리기 전에 멈춘다."""
    model = write(tmp_path / "m.jsonl", [row("x1", "DEAD", "v")])
    llm = write(tmp_path / "l.jsonl", [row("x1", "DEAD", "w")])

    with pytest.raises(ValueError, match="같은 record_id 가 두 번 나온다: x1"):
        apply_report.compare([model], [llm, llm])
    with pytest.raises(ValueError, match="같은 record_id 가 두 번 나온다: x1"):
        apply_report.compare([model, model], [llm])
    with pytest.raises(ValueError, match="같은 record_id 가 두 번 나온다: x1"):
        apply_report.distribution([llm, llm], labeled=set())


def test_missing_labels_file_is_refused(tmp_path, capsys):
    """라벨 파일이 없으면 확정 500건 겹침이 조용히 0 이 된다 - 보고서를 쓰지 않는다."""
    model = write(tmp_path / "m.jsonl", [row("x1", "DEAD", "v")])
    out = tmp_path / "report.json"
    args = ["--model-only", str(model), "--with-llm", str(model), "--json", str(out)]

    code = apply_report.main(args + ["--labels", str(tmp_path / "none.jsonl")])

    assert code == 2
    assert "라벨 파일이 없다" in capsys.readouterr().err
    assert not out.exists()


def test_output_without_the_training_repo_flag_is_refused(tmp_path):
    """플래그 전 출력이면 그룹을 나눌 수 없다 - 조용히 한쪽으로 몰지 않는다."""
    old = {k: v for k, v in row("x1", "DEAD", "r1+m2").items() if k != "training_repo"}
    path = write(tmp_path / "m.jsonl", [old])

    with pytest.raises(ValueError, match="training_repo 가 없는 행 x1"):
        apply_report.distribution([path], labeled=set())


def test_distribution_counts_versions_labels_groups_and_human_overlap(tmp_path):
    path = write(
        tmp_path / "m.jsonl",
        [
            row("x1", "DEAD", "r1+m2"),
            row("x2", "DEAD", "r1+m2", grade="UNKNOWN"),
            row("x3", "UNK", "r1+m2", training=False),
        ],
    )

    result = apply_report.distribution([path], labeled={"x2"})

    assert result["n"] == 3
    assert result["versions"] == {"r1+m2": 3}
    assert result["grades"] == {"INFERRED": 2, "UNKNOWN": 1}
    assert result["labels_by_repo_group"] == {
        "training_repos": {"DEAD": 2},
        "new_repos": {"UNK": 1},
    }
    assert result["human_labeled_overlap"] == 1
