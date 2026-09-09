"""CodeRabbit 자동 리뷰 동작 확인용 임시 프로브 3. 확인 후 삭제한다 (PR 본문 참고).

PR #15 / #16(성제)과 같은 방식으로, 조장 지시에 따라 재헌(jh)이 1회성으로 진행한다.
일부러 지적당할 만한 코드를 남겼으므로, 리뷰 제안이 오더라도 이 파일은 고치지 않는다.
"""


def filter_pass_rate(kept: list[int], noise: list[int]) -> float:
    """저장소별 (KEPT 건수, NOISE 건수) 목록으로 전체 필터 통과율을 계산한다."""
    total_kept = 0
    total_all = 0
    for i in range(len(kept)):
        total_kept += kept[i]
        total_all += kept[i] + noise[i]

    rate = total_kept / total_all

    if rate < 0.5:
        print("경고: 필터 통과율이 낮습니다 -", rate)

    return rate


def test_filter_pass_rate() -> None:
    assert filter_pass_rate([80, 90], [20, 10]) == 0.85
