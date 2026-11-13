"""The retired ID-only launcher cannot access protected accounts or mutate data."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def launcher(monkeypatch):
    path = (
        Path(__file__).resolve().parents[1]
        / "frontend/scripts/verify_recommendations.py"
    )
    spec = spec_from_file_location("frontend_recommendation_verification", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "option", [None, "--page", "--library", "--library-sync", "--preferences"]
)
def test_id_only_launcher_exits_before_any_side_effect(
    launcher, monkeypatch, capsys, tmp_path, option
):
    import subprocess

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["verify_recommendations.py"] + ([option] if option else [])
    )
    run = Mock(
        side_effect=AssertionError("No container, HTTP or browser process allowed")
    )
    monkeypatch.setattr(subprocess, "run", run)
    assert launcher.main() == 2
    assert "authenticated owner session" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
    run.assert_not_called()


def test_old_deployment_arguments_do_not_enable_the_retired_launcher(
    launcher, monkeypatch
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_recommendations.py",
            "--project-name",
            "project",
            "--base-url",
            "http://localhost:5173",
            "--library",
        ],
    )
    assert launcher.main() == 2


def test_conflicting_modes_are_still_rejected(launcher, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["verify_recommendations.py", "--library", "--library-sync"]
    )
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 2
