"""CV launch paths must work without loading models or opening a camera."""

from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from backend.cv import main


@pytest.mark.parametrize("module", [False, True])
def test_launch_help(module):
    backend = Path(__file__).resolve().parents[2]
    args = ["-m", "backend.cv"] if module else ["cv/main.py"]
    result = subprocess.run(
        [sys.executable, *args, "--help"],
        cwd=backend.parent if module else backend,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--check-env" in result.stdout


def test_environment_check_does_not_start_service():
    with (
        patch.object(sys, "argv", ["cv/main.py", "--check-env"]),
        patch("backend.scripts.check_cv_environment.main") as check,
        patch.object(main, "build_providers") as providers,
        pytest.raises(SystemExit) as exit_info,
    ):
        main.cli()
    assert exit_info.value.code == 0
    check.assert_called_once_with()
    providers.assert_not_called()
