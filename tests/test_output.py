from cli.main import inline_detail_list


def test_inline_detail_list() -> None:
    first, second = "a" * 80, "b" * 50
    assert inline_detail_list([first, second]) == f"{first}\n{second}"
    assert inline_detail_list(["x" * 121]) == "x" * 121
    assert inline_detail_list([]) == "none"
