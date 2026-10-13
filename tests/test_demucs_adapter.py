from pathlib import Path

import pytest

from contracts import AdapterError, FailureCategory, SeparationOptions
from demucs_adapter import DemucsSeparator


def test_separator_reports_model_names_and_supported_options():
    separator = DemucsSeparator()

    models = separator.available_models()

    assert {model.name for model in models} == {"htdemucs_6s"}
    assert "htdemucs_6s" in {model.name for model in models}
    six_stem = next(model for model in models if model.name == "htdemucs_6s")
    assert six_stem.stems == ("vocals", "drums", "bass", "guitar", "piano", "other")
    assert set(six_stem.parameters) == {"shifts", "overlap", "segment"}


def test_separator_passes_selected_model_device_and_parameters(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, *, check):
        calls.append((command, check))
        result = tmp_path / "output" / "htdemucs_6s" / "source"
        result.mkdir(parents=True)
        for name in ("vocals", "drums", "bass", "guitar", "piano", "other"):
            (result / f"{name}.wav").write_bytes(b"wav")

    monkeypatch.setattr("demucs_adapter.subprocess.run", fake_run)
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF0000WAVE")
    options = SeparationOptions("htdemucs_6s", "cpu", {"shifts": 2, "overlap": 0.5})

    result = DemucsSeparator().separate(source, tmp_path / "output", options)

    command, checked = calls[0]
    assert checked is True
    assert command[command.index("-n") + 1] == "htdemucs_6s"
    assert command[command.index("-d") + 1] == "cpu"
    assert command[command.index("--shifts") + 1] == "2"
    assert command[command.index("--overlap") + 1] == "0.5"
    assert len(result.stems) == 6


@pytest.mark.parametrize(
    "options",
    [
        SeparationOptions("made-up-model", "cpu"),
        SeparationOptions("htdemucs_6s", "cpu", {"unknown": 1}),
        SeparationOptions("htdemucs_6s", "cpu", {"shifts": -1}),
        SeparationOptions("htdemucs_6s", "cpu", {"overlap": 2}),
    ],
)
def test_separator_rejects_unsupported_model_or_parameters(options, tmp_path):
    with pytest.raises(AdapterError) as error:
        DemucsSeparator().separate(tmp_path / "source.wav", tmp_path / "out", options)

    assert error.value.category is FailureCategory.INVALID_INPUT


def test_separator_reports_missing_demucs_outputs_as_permanent(monkeypatch, tmp_path):
    monkeypatch.setattr("demucs_adapter.subprocess.run", lambda *args, **kwargs: None)
    source = tmp_path / "source.wav"
    source.touch()

    with pytest.raises(AdapterError) as error:
        DemucsSeparator().separate(source, tmp_path / "out", SeparationOptions("htdemucs_6s", "cpu"))

    assert error.value.category is FailureCategory.PERMANENT
