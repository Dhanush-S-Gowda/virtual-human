"""Setup never downloads missing assets until the user types Y."""

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.scripts import setup_models as setup


@pytest.mark.parametrize("reply, accepted", [("Y", True), ("y", True), (" y ", True),
                                             ("yes", False), ("", False), ("N", False)])
def test_only_explicit_y_authorizes_a_download(monkeypatch, reply, accepted):
    monkeypatch.setattr("builtins.input", lambda _: reply)
    assert setup.confirm("model") is accepted


def test_closed_stdin_skips_download(monkeypatch):
    def eof(_):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert not setup.confirm("model")


def test_existing_asset_never_prompts_or_downloads(capsys):
    prompt, download = MagicMock(), MagicMock()
    assert setup.prepare(setup.Asset("model", lambda: True, download), prompt) == "existing"
    prompt.assert_not_called()
    download.assert_not_called()
    assert "already downloaded" in capsys.readouterr().out


def test_missing_asset_can_be_declined_or_installed():
    ready = False
    def download():
        nonlocal ready
        ready = True
    installer = MagicMock(side_effect=download)
    asset = setup.Asset("model", lambda: ready, installer)
    assert setup.prepare(asset, lambda _: False) == "skipped"
    installer.assert_not_called()
    assert setup.prepare(asset, lambda _: True) == "downloaded"
    assert ready


def test_incomplete_download_is_not_reported_as_installed():
    with pytest.raises(RuntimeError, match="required files"):
        setup.prepare(setup.Asset("model", lambda: False, lambda: None), lambda _: True)


@pytest.mark.parametrize("gpu, variant", [(True, "gpu"), (False, "cpu")])
@pytest.mark.parametrize("exists, approved", [(True, False), (False, False), (False, True)])
def test_kokoro_selects_same_version_and_only_pulls_with_approval(monkeypatch, gpu, variant, exists, approved):
    monkeypatch.setattr(setup.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(setup, "nvidia_gpu_available", lambda: gpu)
    image = f"ghcr.io/remsky/kokoro-fastapi-{variant}:v0.9.2"
    downloaded = False
    def probe(command):
        if "inspect" in command:
            assert command[-1] == image
            return SimpleNamespace(returncode=0 if exists or downloaded else 1)
        return SimpleNamespace(returncode=0)
    def pull(command, **kwargs):
        nonlocal downloaded
        assert command == ["docker", "pull", image]
        assert kwargs["check"]
        downloaded = True
    monkeypatch.setattr(setup, "run_probe", probe)
    downloader = MagicMock(side_effect=pull)
    monkeypatch.setattr(setup.subprocess, "run", downloader)
    approve = MagicMock(return_value=approved)
    result = setup.prepare_kokoro(approve)
    if exists:
        assert result == "existing"
        approve.assert_not_called()
    elif approved:
        assert result == "downloaded"
    else:
        assert result == "skipped"
    assert downloader.call_count == int(not exists and approved)


def test_failed_docker_pull_is_visible(monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(setup, "nvidia_gpu_available", lambda: False)
    monkeypatch.setattr(setup, "run_probe", lambda command: SimpleNamespace(returncode=int("inspect" in command)))
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(setup.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="Kokoro image setup failed"):
        setup.prepare_kokoro(lambda _: True)


def test_unavailable_docker_never_prompts_or_pulls(monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(setup, "run_probe", lambda _: SimpleNamespace(returncode=1))
    prompt = MagicMock()
    assert setup.prepare_kokoro(prompt) == "skipped"
    prompt.assert_not_called()


def test_nvidia_query_and_cuda_fallback(monkeypatch):
    import sys
    monkeypatch.setattr(setup.shutil, "which", lambda _: "nvidia-smi")
    monkeypatch.setattr(setup, "run_probe", lambda _: SimpleNamespace(returncode=0, stdout="NVIDIA test GPU\n"))
    assert setup.nvidia_gpu_available()
    monkeypatch.setattr(setup.shutil, "which", lambda _: None)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        version=SimpleNamespace(cuda=None), cuda=SimpleNamespace(is_available=lambda: True)))
    assert not setup.nvidia_gpu_available()  # ROCm is not the CUDA image.


@pytest.mark.parametrize("dependency_state, reply, installs, invokes_helper", [
    (0, "", False, True), (1, "N\n", False, False), (1, "\n", False, False),
    (1, "Y\n", True, True), (1, "yes\n", False, False),
])
def test_bash_dependency_gate_is_offline_and_never_auto_downloads(tmp_path, dependency_state, reply, installs, invokes_helper):
    root = Path(__file__).resolve().parents[2]
    log = tmp_path / "commands"
    uv = tmp_path / "uv"
    uv.write_text('''#!/bin/sh
printf '%s\n' "$*" >> "$COMMAND_LOG"
case "$*" in
  'sync --check --offline --no-python-downloads') exit "$DEPENDENCY_STATE" ;;
esac
''')
    uv.chmod(0o755)
    result = subprocess.run(["bash", str(root / "backend/setup.sh")], input=reply,
                            env={**os.environ, "PATH": str(tmp_path) + ":/usr/bin:/bin",
                                 "COMMAND_LOG": str(log), "DEPENDENCY_STATE": str(dependency_state)},
                            text=True, capture_output=True, check=True, cwd=tmp_path)
    calls = log.read_text().splitlines()
    assert calls[0] == "sync --check --offline --no-python-downloads"
    assert ("sync" in calls) == installs
    assert any("--no-sync --offline --no-python-downloads" in call for call in calls) == invokes_helper
    if dependency_state == 0:
        assert "already installed" in result.stdout


def test_faster_whisper_probe_is_local_only_and_reuses_complete_cache(monkeypatch, tmp_path):
    import faster_whisper.utils
    monkeypatch.setattr(setup.settings, "faster_whisper_model", "base")
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
        (tmp_path / name).write_bytes(b"existing")
    lookup = MagicMock(return_value=str(tmp_path))
    monkeypatch.setattr(faster_whisper.utils, "download_model", lookup)
    asset = setup.whisper_asset()
    prompt = MagicMock()
    assert setup.prepare(asset, prompt) == "existing"
    assert lookup.call_args.kwargs["local_files_only"] is True
    prompt.assert_not_called()


def test_partial_faster_whisper_cache_does_not_download_when_declined(monkeypatch, tmp_path):
    import faster_whisper.utils
    monkeypatch.setattr(setup.settings, "faster_whisper_model", "base")
    (tmp_path / "config.json").write_bytes(b"partial")
    lookup = MagicMock(return_value=str(tmp_path))
    monkeypatch.setattr(faster_whisper.utils, "download_model", lookup)
    assert setup.prepare(setup.whisper_asset(), lambda _: False) == "skipped"
    assert all(call.kwargs["local_files_only"] for call in lookup.call_args_list)


def test_supertonic_requires_complete_runtime_assets_before_skipping(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "BACKEND_DIR", tmp_path)
    asset = setup.supertonic_asset()
    assert not asset.present()
    root = tmp_path / "supertonic/assets"
    for name in ("duration_predictor.onnx", "text_encoder.onnx", "vector_estimator.onnx",
                 "vocoder.onnx", "tts.json", "unicode_indexer.json"):
        path = root / "onnx" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"existing")
    assert not asset.present()
    for name in ("F1.json", "M1.json"):
        path = root / "voice_styles" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"existing")
    prompt = MagicMock()
    assert setup.prepare(asset, prompt) == "existing"
    prompt.assert_not_called()


def test_declining_all_models_never_invokes_any_download(monkeypatch):
    download = MagicMock()
    monkeypatch.setattr(setup, "whisper_asset", lambda: setup.Asset("optional STT", lambda: False, download))
    monkeypatch.setattr(setup, "supertonic_asset", lambda: setup.Asset("optional TTS", lambda: False, download))
    monkeypatch.setattr(setup, "cv_assets", lambda: [setup.Asset("CV model", lambda: False, download)])
    monkeypatch.setattr(setup, "prepare_kokoro", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _: "N")
    setup.main()
    download.assert_not_called()
