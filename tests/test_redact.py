from cli.main import ResolvedValue, redact


def test_common_secret_keys() -> None:
    secret = ResolvedValue("0123456789", ".env")
    for key in (
        "DATABASE_PASSWORD",
        "DB_PASSWD",
        "SSH_PASSPHRASE",
        "AUTH_TOKEN",
        "CLIENT_SECRET",
        "API_KEY",
        "PRIVATE_KEY",
        "AWS_ACCESS_KEY",
        "CREDENTIALS",
    ):
        assert redact(key, secret) == "0********9"
    assert redact("MONKEY", secret) == "0123456789"
    assert redact("TOKEN", ResolvedValue("short", ".env")) == "*****"


if __name__ == "__main__":
    test_common_secret_keys()
