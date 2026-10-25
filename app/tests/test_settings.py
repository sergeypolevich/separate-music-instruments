from pathlib import Path

import pytest

from audio_stems.config import Settings, default_storage_root, move_legacy_storage


def test_settings_defaults_preserve_current_cli_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings = Settings.load(environ={}, dotenv_path=tmp_path / "missing.env")

    assert settings.storage_root == tmp_path / "Audio Stems" / "Music"
    assert settings.temp_root == settings.storage_root / "_demucs_temp"
    assert settings.retry_count == 3
    assert settings.retry_delays == (3.0, 6.0)
    assert settings.model_name == "hdemucs_mmi"
    assert settings.model_device == "auto"
    assert settings.log_level == "INFO"
    assert settings.log_max_bytes == 10 * 1024 * 1024
    assert settings.log_backup_count == 5
    assert settings.api_port == 8000


def test_dotenv_values_load_and_process_environment_takes_precedence(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AUDIO_STEMS_LOG_LEVEL=DEBUG\n"
        "AUDIO_STEMS_RETRY_COUNT=4\n"
        "AUDIO_STEMS_MODEL_DEVICE='cpu'\n",
        encoding="utf-8",
    )

    settings = Settings.load(
        environ={"AUDIO_STEMS_RETRY_COUNT": "2"},
        dotenv_path=env_file,
    )

    assert settings.log_level == "DEBUG"
    assert settings.retry_count == 2
    assert settings.model_device == "cpu"  # quoted .env values are unquoted


@pytest.mark.parametrize(
    "environment",
    [
        {"AUDIO_STEMS_LOG_LEVEL": "TRACE"},
        {"AUDIO_STEMS_RETRY_COUNT": "0"},
        {"AUDIO_STEMS_LOG_MAX_BYTES": "not-an-integer"},
        {"AUDIO_STEMS_ALLOWED_SOURCE_HOSTS": "youtube.com/path"},
        {"AUDIO_STEMS_API_PORT": "0"},
        {"AUDIO_STEMS_API_PORT": "not-a-port"},
    ],
)
def test_invalid_settings_are_rejected(environment, tmp_path):
    with pytest.raises(ValueError):
        Settings.load(environ=environment, dotenv_path=tmp_path / "missing.env")


def test_api_port_can_be_overridden_for_isolated_local_runs(tmp_path):
    settings = Settings.load(
        environ={"AUDIO_STEMS_API_PORT": "8123"},
        dotenv_path=tmp_path / "missing.env",
    )

    assert settings.api_port == 8123


def test_media_folder_moves_from_youtube_audio_to_music_once_and_only_by_default(tmp_path):
    base = tmp_path / "Audio Stems"
    legacy, music = base / "YouTube Audio", base / "Music"
    (legacy / "Stems" / "Song").mkdir(parents=True)
    (legacy / "Song.webm").write_bytes(b"audio")
    no_env = tmp_path / "missing.env"

    assert default_storage_root(tmp_path) == legacy  # not moved yet: the app keeps using it
    configured = {"AUDIO_STEMS_STORAGE_ROOT": str(legacy)}
    assert move_legacy_storage(home=tmp_path, environ=configured, dotenv_path=no_env) is False
    assert legacy.is_dir()  # a configured root is never moved

    assert move_legacy_storage(home=tmp_path, environ={}, dotenv_path=no_env) is True
    assert (music / "Song.webm").read_bytes() == b"audio" and (music / "Stems" / "Song").is_dir()
    assert not legacy.exists() and default_storage_root(tmp_path) == music
    assert move_legacy_storage(home=tmp_path, environ={}, dotenv_path=no_env) is False  # nothing left to move

    legacy.mkdir()  # both exist: nothing is merged or overwritten
    assert move_legacy_storage(home=tmp_path, environ={}, dotenv_path=no_env) is False
    assert legacy.is_dir() and (music / "Song.webm").exists() and default_storage_root(tmp_path) == music


def test_media_folder_in_use_keeps_its_old_name(tmp_path, monkeypatch, capsys):
    legacy = tmp_path / "Audio Stems" / "YouTube Audio"
    legacy.mkdir(parents=True)

    def locked(*_args):
        raise PermissionError("in use")

    monkeypatch.setattr("audio_stems.config.os.replace", locked)
    assert move_legacy_storage(home=tmp_path, environ={}, dotenv_path=tmp_path / "missing.env") is False
    assert legacy.is_dir() and default_storage_root(tmp_path) == legacy
    assert "using the old folder" in capsys.readouterr().out
