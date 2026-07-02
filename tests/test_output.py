from cli.main import detail_list


def test_inline_detail_list() -> None:
    assert detail_list(["one", "two"], " ") == "one two"
    assert detail_list([], " ") == "none"
