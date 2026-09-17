from types import SimpleNamespace

from cli import main


def make_args(**overrides) -> SimpleNamespace:
    values = {"env": [], "env_file": []}
    values.update(overrides)
    return SimpleNamespace(**values)


def make_config(**overrides) -> dict:
    values = {
        "CODEMATE_DOCKER_PARAMS": "",
        "CODEMATE_MOUNTS": "",
        "TZ": "UTC",
        "CODEMATE_AGENT": "claude",
        "CODEMATE_IMAGE": main.DEFAULT_IMAGE,
        "CODEMATE_IMAGE_REGISTRY": main.DEFAULT_IMAGE_REGISTRY,
    }
    values.update(overrides)
    return {
        key: main.ResolvedValue(value, "test", main.FIELD_BY_NAME[key])
        for key, value in values.items()
    }


def test_default_image_comes_from_ghcr_io(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CODEMATE_IMAGE_REGISTRY", raising=False)
    monkeypatch.delenv("CODEMATE_IMAGE", raising=False)

    config = main.resolve_config(make_args(), tmp_path)

    assert config["CODEMATE_IMAGE_REGISTRY"].value == "ghcr.io"
    assert config["CODEMATE_IMAGE"].value == main.DEFAULT_IMAGE
    assert config["CODEMATE_IMAGE"].source == "default"


def test_registry_replaces_ghcr_io_in_the_default_image(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEMATE_IMAGE_REGISTRY", "mirror.example.com/codemate")
    monkeypatch.delenv("CODEMATE_IMAGE", raising=False)

    config = main.resolve_config(make_args(), tmp_path)

    assert config["CODEMATE_IMAGE"].value == "mirror.example.com/codemate/boringhappy/codemate:latest"
    assert config["CODEMATE_IMAGE"].source == "default"


def test_registry_can_come_from_the_project_env(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CODEMATE_IMAGE_REGISTRY", raising=False)
    monkeypatch.delenv("CODEMATE_IMAGE", raising=False)
    (tmp_path / ".env").write_text("CODEMATE_IMAGE_REGISTRY=registry.internal:5000/\n")

    config = main.resolve_config(make_args(), tmp_path)

    assert config["CODEMATE_IMAGE"].value == "registry.internal:5000/boringhappy/codemate:latest"


def test_explicit_image_is_never_rewritten(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CODEMATE_IMAGE_REGISTRY", "mirror.example.com")
    monkeypatch.setenv("CODEMATE_IMAGE", "ghcr.io/other/codemate:tag")

    config = main.resolve_config(make_args(), tmp_path)

    assert config["CODEMATE_IMAGE"].value == "ghcr.io/other/codemate:tag"
    assert config["CODEMATE_IMAGE"].source == "environment"


def test_default_image_helper_normalizes_the_registry() -> None:
    assert main.default_image("") == main.DEFAULT_IMAGE
    assert main.default_image("  ") == main.DEFAULT_IMAGE
    assert main.default_image("mirror.example.com/") == "mirror.example.com/boringhappy/codemate:latest"
    assert (
        main.default_image("mirror.example.com", main.DEFAULT_PURE_IMAGE_REPOSITORY)
        == "mirror.example.com/boringhappy/codemate-pure:latest"
    )


def test_pure_image_follows_the_registry() -> None:
    config = make_config(
        CODEMATE_IMAGE=main.DEFAULT_IMAGE,
        CODEMATE_IMAGE_REGISTRY="mirror.example.com",
    )

    assert main.pure_image(config) == "mirror.example.com/boringhappy/codemate-pure:latest"


def test_pure_image_without_a_registry_keeps_ghcr_io() -> None:
    config = make_config(CODEMATE_IMAGE_REGISTRY="")

    assert main.pure_image(config) == main.DEFAULT_PURE_IMAGE


def test_pure_image_still_prefers_an_explicit_image() -> None:
    config = make_config(
        CODEMATE_IMAGE=main.DEFAULT_IMAGE,
        CODEMATE_IMAGE_REGISTRY="mirror.example.com",
    )
    config["CODEMATE_IMAGE"] = main.ResolvedValue(
        "codemate-pure:dev", "cli", main.FIELD_BY_NAME["CODEMATE_IMAGE"]
    )

    assert main.pure_image(config) == "codemate-pure:dev"


def test_docker_command_uses_the_registry_image(monkeypatch, tmp_path) -> None:
    home = tmp_path / "codemate-home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("CODEMATE_HOME", str(home))
    monkeypatch.setenv("CODEMATE_IMAGE_REGISTRY", "mirror.example.com")
    monkeypatch.delenv("CODEMATE_IMAGE", raising=False)
    monkeypatch.chdir(tmp_path)

    args = SimpleNamespace(mount=[], docker_param=[], dry_run=True, shell=False)
    config = main.resolve_config(make_args(), tmp_path)
    cmd = main.docker_command(config, args, "/tmp/codemate.env")

    assert cmd[-1] == "mirror.example.com/boringhappy/codemate:latest"
