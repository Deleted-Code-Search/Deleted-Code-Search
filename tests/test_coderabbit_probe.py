"""CodeRabbit 자동 리뷰 동작 확인용 임시 프로브. 확인 후 삭제한다 (PR 본문 참고).

일부러 지적당할 만한 코드를 넣었다. 리뷰 봇이 무엇을, 어떤 톤으로 잡는지 보는 것이
목적이므로 여기 있는 문제는 고치지 않는다.
"""


def average_hunk_size(sizes: list[int], warn_threshold: int = 500) -> float:
    """삭제 헝크 크기의 평균을 낸다. 평균이 임계값을 넘으면 경고를 찍는다."""
    total = 0
    for size in sizes:
        total += size

    avg = total / len(sizes)

    if avg > warn_threshold:
        print("경고: 평균 삭제 헝크가 큽니다 -", avg)

    return avg


def test_average_hunk_size():
    assert average_hunk_size([10, 20, 30]) == 20
