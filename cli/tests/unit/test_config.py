import stat
from pathlib import Path

from testoria_cli import config as config_module
from testoria_cli.config import Config


def test_flag_beats_env_and_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config_module.save(Config(url="https://from-file"), path)

    resolved = config_module.resolve(
        url="https://from-flag",
        path=path,
        env={"TESTORIA_URL": "https://from-env"},
    )

    assert resolved.url == "https://from-flag"


def test_env_beats_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config_module.save(Config(url="https://from-file"), path)

    resolved = config_module.resolve(
        path=path, env={"TESTORIA_URL": "https://from-env"}
    )

    assert resolved.url == "https://from-env"


def test_file_is_the_fallback(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config_module.save(Config(url="https://from-file"), path)

    assert config_module.resolve(path=path, env={}).url == "https://from-file"


def test_trailing_slash_is_stripped(tmp_path: Path) -> None:
    resolved = config_module.resolve(
        url="https://api.example.com/", path=tmp_path / "none.yaml", env={}
    )
    assert resolved.url == "https://api.example.com"


def test_api_key_is_never_written_to_disk(tmp_path: Path) -> None:
    """A CI secret must not leak into a mounted home directory."""
    path = tmp_path / "config.yaml"
    config_module.save(
        Config(url="https://x", access_token="jwt-token"), path
    )

    contents = path.read_text()

    assert "jwt-token" in contents
    assert "api_key" not in contents
    # And the resolver only ever sources it from flag/env.
    resolved = config_module.resolve(
        path=path, env={"TESTORIA_API_KEY": "tsk_a_b"}
    )
    assert resolved.api_key == "tsk_a_b"
    assert "tsk_a_b" not in path.read_text()


def test_config_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config_module.save(Config(url="https://x", access_token="t"), path)

    mode = stat.S_IMODE(path.stat().st_mode)

    assert mode == 0o600


def test_api_key_suppresses_a_stored_jwt(tmp_path: Path) -> None:
    """Only one credential is ever sent — the server 400s on both."""
    path = tmp_path / "config.yaml"
    config_module.save(Config(url="https://x", access_token="jwt"), path)

    resolved = config_module.resolve(
        path=path, env={"TESTORIA_API_KEY": "tsk_a_b"}
    )

    assert resolved.api_key == "tsk_a_b"
    assert resolved.access_token is None
    assert resolved.via == "api_key"


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    resolved = config_module.resolve(path=tmp_path / "absent.yaml", env={})
    assert resolved.url == ""
    assert resolved.api_key is None


def test_clear_removes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    config_module.save(Config(url="https://x"), path)

    config_module.clear(path)

    assert not path.exists()
    config_module.clear(path)  # idempotent
