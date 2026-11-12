"""Keep personal identifiers out of live verification failure diagnostics."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

USER = "76561190000000001"  # Synthetic.


@pytest.fixture
def launcher(monkeypatch):
    path = (
        Path(__file__).resolve().parents[1]
        / "frontend/scripts/verify_recommendations.py"
    )
    spec = spec_from_file_location("frontend_recommendation_verification", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("sys.argv", [str(path)])
    return module


def test_browser_failure_output_is_redacted_and_id_is_not_an_argument(
    launcher, monkeypatch, capsys
):
    run = Mock(
        side_effect=[
            SimpleNamespace(returncode=0, stdout=USER + "\n"),
            SimpleNamespace(
                returncode=1,
                stdout=f"Failed request for {USER}\n",
                stderr=f"Input: {USER}\n",
            ),
        ]
    )
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.main() == 1
    output = capsys.readouterr().out
    assert USER not in output
    assert output.count("[redacted]") == 2
    command = run.call_args.args[0]
    assert USER not in command
    docker_env = [
        command[index + 1] for index, value in enumerate(command) if value == "-e"
    ]
    assert docker_env == ["GAMEREC_TEST_STEAM_ID", "PLAYWRIGHT_BASE_URL"]
    assert run.call_args.kwargs["env"]["GAMEREC_TEST_STEAM_ID"] == USER


def test_configuration_failure_does_not_print_captured_diagnostics(
    launcher, monkeypatch, capsys
):
    run = Mock(
        return_value=SimpleNamespace(
            returncode=1, stdout=USER, stderr=f"private: {USER}"
        )
    )
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.main() == 1
    assert USER not in capsys.readouterr().out
    run.assert_called_once()


def test_page_mode_uses_private_launcher_and_separate_browser_suite(
    launcher, monkeypatch
):
    monkeypatch.setattr("sys.argv", ["verify_recommendations.py", "--page"])
    run = Mock(
        side_effect=[
            SimpleNamespace(returncode=0, stdout=USER + "\n"),
            SimpleNamespace(returncode=0, stdout="Passed\n", stderr=""),
        ]
    )
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.main() == 0
    assert run.call_args.args[0][-1] == "test:recommendations-page:live"
    assert USER not in run.call_args.args[0]


def test_library_mode_uses_private_environment_and_redacts_artifacts(
    launcher, monkeypatch
):
    monkeypatch.setattr("sys.argv", ["verify_recommendations.py", "--library"])
    run = Mock(
        side_effect=[
            SimpleNamespace(returncode=0, stdout=USER + "\n"),
            SimpleNamespace(returncode=0, stdout="Passed\n", stderr=""),
        ]
    )
    monkeypatch.setattr(launcher.subprocess, "run", run)
    redact = Mock()
    monkeypatch.setattr(launcher, "redact_library_artifacts", redact)
    assert launcher.main() == 0
    assert run.call_args.args[0][-1] == "test:library:live"
    assert USER not in run.call_args.args[0]
    assert run.call_args.kwargs["env"]["GAMEREC_TEST_STEAM_ID"] == USER
    assert redact.call_args.args[0].name == "library"
    assert redact.call_args.args[1] == USER


def test_library_failure_artifact_redaction_preserves_other_content(launcher, tmp_path):
    nested = tmp_path / "failed-case"
    nested.mkdir()
    diagnostic = nested / "error-context.md"
    diagnostic.write_text(f"Viewing library {USER}\n/api/users/{USER}/library\n")
    evidence = nested / "verification.json"
    evidence.write_text('{"status": 200}')
    image = nested / "desktop.png"
    image.write_bytes(b"masked screenshot placeholder")
    launcher.redact_library_artifacts(tmp_path, USER)
    assert USER not in diagnostic.read_text()
    assert diagnostic.read_text().count("[redacted]") == 2
    assert evidence.read_text() == '{"status": 200}'
    assert image.read_bytes() == b"masked screenshot placeholder"


def test_library_mode_rejects_non_17_digit_configuration(launcher, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["verify_recommendations.py", "--library"])
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="123\n"))
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.main() == 1
    assert "17-digit" in capsys.readouterr().out
    run.assert_called_once()
