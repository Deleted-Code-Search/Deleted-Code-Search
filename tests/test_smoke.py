"""CI 배선 확인용 스모크 테스트. 패키지가 import되는지만 본다."""


def test_packages_import() -> None:
    import classify  # noqa: F401
    import pipeline  # noqa: F401
    import pipeline.parsers  # noqa: F401
    import search  # noqa: F401
