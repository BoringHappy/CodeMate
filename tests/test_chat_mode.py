from types import SimpleNamespace

from cli.main import FIELD_BY_NAME, ResolvedValue, chat_defaults, collect_cli_values


def test_collect_cli_values_includes_chat_flag() -> None:
    values = collect_cli_values(SimpleNamespace(chat=True))

    assert values["chat"] == "true"


def test_chat_defaults_sets_no_pr() -> None:
    config = {
        "CODEMATE_CHAT": ResolvedValue("true", "cli", FIELD_BY_NAME["CODEMATE_CHAT"]),
        "CODEMATE_NO_PR": ResolvedValue("", "default", FIELD_BY_NAME["CODEMATE_NO_PR"]),
    }

    chat_defaults(config)

    assert config["CODEMATE_NO_PR"].value == "true"
    assert config["CODEMATE_NO_PR"].source == "chat"
