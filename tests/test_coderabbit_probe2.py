"""CodeRabbit 자동 리뷰 동작 확인용 임시 프로브 2. 확인 후 삭제한다 (PR 본문 참고).

레포가 org(Deleted-Code-Search)로 이전된 뒤 자동 리뷰가 붙는지 다시 본다.
일부러 지적당할 만한 코드를 넣었으므로 여기 있는 문제는 고치지 않는다.
"""


def extract_reason(commit_message: str, marker: str = "Refs") -> str:
    """커밋 메시지 본문에서 마커 앞부분을 삭제 이유로 잘라낸다."""
    lines = commit_message.split("\n")

    body = ""
    for line in lines[1:]:
        body = body + line + "\n"

    idx = body.find(marker)
    reason = body[0:idx]

    try:
        return reason.strip()
    except Exception:
        return ""


def test_extract_reason():
    message = "chore(repo): 제목\n\n웹 설정 한 곳으로 통일했다.\nRefs #10\n"
    assert extract_reason(message) == "웹 설정 한 곳으로 통일했다."
