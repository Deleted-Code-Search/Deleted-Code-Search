"""tools/repo_scale.py 의 순수 계산 테스트 (네트워크 없음, 이슈 #171)."""

from tools import repo_scale as rs


def node(files=1, add=10, delete=5, parents=1, prs=()):
    return {
        "oid": "x",
        "additions": add,
        "deletions": delete,
        "changedFilesIfAvailable": files,
        "parents": {"totalCount": parents},
        "associatedPullRequests": {
            "nodes": [{"number": number, "mergedAt": merged} for number, merged in prs]
        },
    }


def test_summarize_year_excludes_merge_commits():
    sample = rs.summarize_year(2024, 500, [node(), node(parents=2, prs=[(1, "t")])])
    assert (sample.sampled, sample.nonmerge, sample.pr_linked) == (2, 1, 0)


def test_summarize_year_counts_only_merged_prs_and_distinct_numbers():
    nodes = [
        node(prs=[(7, "2024-01-01")]),
        node(prs=[(7, "2024-01-01")]),
        node(prs=[(8, None)]),  # 열린 PR 은 연결로 세지 않는다
    ]
    sample = rs.summarize_year(2024, 3, nodes)
    assert (sample.pr_linked, sample.distinct_prs) == (2, 1)


def test_summarize_year_bulk_threshold_and_churn():
    nodes = [node(files=rs.BULK_FILES, add=900, delete=100), node(files=rs.BULK_FILES - 1)]
    sample = rs.summarize_year(2024, 2, nodes)
    assert sample.bulk == 1
    assert (sample.churn, sample.bulk_churn) == (1015, 1000)


def test_estimate_window_weights_by_year_total():
    # 2023: 1000커밋 중 표본 2건 모두 PR 연결 / 2024: 100커밋 중 표본 2건 모두 연결 없음
    years = [
        rs.YearSample(2023, 1000, sampled=2, nonmerge=2, pr_linked=2, distinct_prs=2),
        rs.YearSample(2024, 100, sampled=2, nonmerge=2),
    ]
    window = rs.estimate_window(years)
    assert window.window_commits_all == 1100
    assert window.est_walk_commits == 1100
    assert window.est_pr_linked_commits == 1000
    assert round(window.pr_linked_share, 3) == 0.909


def test_estimate_window_merge_share_reduces_walk_commits():
    years = [rs.YearSample(2024, 1000, sampled=4, nonmerge=3)]
    assert rs.estimate_window(years).est_walk_commits == 750


def test_estimate_window_without_samples():
    window = rs.estimate_window([rs.YearSample(2024, 0)])
    assert window.est_walk_commits == 0
    assert window.pr_linked_share is None
    assert window.bulk_commit_share is None


def test_estimate_api_calls_orders_low_mid_high():
    ff_mid, low, mid, high = rs.estimate_api_calls(10_000, 1.0, 0.5)
    assert ff_mid == 750
    assert (low, mid, high) == (800, 1875, 3150)


def test_estimate_api_calls_missing_ratios_count_commit_lookup_only():
    _, low, mid, high = rs.estimate_api_calls(1000, None, None)
    assert (low, mid, high) == (32, 75, 126)


def test_scale_flag():
    assert rs.scale_flag(115_000, 900) == "XL"
    assert rs.scale_flag(17_000, 6500) == "XL"
    assert rs.scale_flag(22_000, 500) == "L"
    assert rs.scale_flag(5_000, 300) == ""


def test_summarize_tree_counts_python_blobs_only():
    tree = [
        {"type": "blob", "path": "a.py", "size": 100},
        {"type": "blob", "path": "pkg/b.py", "size": 300},
        {"type": "blob", "path": "c.md", "size": 999},
        {"type": "tree", "path": "pkg"},
    ]
    assert rs.summarize_tree(tree) == (2, 400, 300)
