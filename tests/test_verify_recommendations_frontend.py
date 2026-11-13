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


def test_sync_mode_is_explicit_and_uses_separate_private_suite(
    launcher, monkeypatch, capsys
):
    monkeypatch.setattr("sys.argv", ["verify_recommendations.py", "--library-sync"])
    run = Mock(
        side_effect=[
            SimpleNamespace(returncode=0, stdout=USER + "\n"),
            SimpleNamespace(returncode=1, stdout=f"Sync failed: {USER}", stderr=""),
        ]
    )
    monkeypatch.setattr(launcher.subprocess, "run", run)
    redact = Mock()
    monkeypatch.setattr(launcher, "redact_library_artifacts", redact)
    assert launcher.main() == 1
    assert run.call_args.args[0][-1] == "test:library-sync:live"
    assert USER not in run.call_args.args[0]
    assert run.call_args.kwargs["env"]["GAMEREC_TEST_STEAM_ID"] == USER
    assert redact.call_args.args[0].name == "library-sync"
    assert redact.call_args.args[1] == USER
    assert USER not in capsys.readouterr().out


def test_sync_mode_rejects_non_17_digit_configuration(launcher, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["verify_recommendations.py", "--library-sync"])
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="123\n"))
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.main() == 1
    assert "17-digit" in capsys.readouterr().out
    run.assert_called_once()


def test_read_only_and_sync_modes_cannot_be_combined(launcher, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["verify_recommendations.py", "--library", "--library-sync"]
    )
    run = Mock()
    monkeypatch.setattr(launcher.subprocess, "run", run)
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 2
    run.assert_not_called()


def test_preferences_mode_dispatch_keeps_identity_private(launcher, monkeypatch):
    monkeypatch.setattr("sys.argv", ["verify_recommendations.py", "--preferences"])
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=USER + "\n"))
    monkeypatch.setattr(launcher.subprocess, "run", run)
    preferences = Mock(return_value=0)
    monkeypatch.setattr(launcher, "run_preferences", preferences)
    assert launcher.main() == 0
    command = preferences.call_args.args[-1]
    assert command[-1] == "test:preferences:live"
    assert USER not in command
    assert preferences.call_args.args[-2]["GAMEREC_TEST_STEAM_ID"] == USER
    assert "GAMEREC_PREFERENCE_STAGE" in command
    assert "GAMEREC_PREFERENCE_CASES" in command
    run.assert_called_once()


def test_preferences_mode_requires_17_digits(launcher, monkeypatch):
    monkeypatch.setattr("sys.argv", ["verify_recommendations.py", "--preferences"])
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="123\n"))
    monkeypatch.setattr(launcher.subprocess, "run", run)
    preferences = Mock()
    monkeypatch.setattr(launcher, "run_preferences", preferences)
    assert launcher.main() == 1
    preferences.assert_not_called()


@pytest.fixture
def preference_workflow(launcher, monkeypatch, tmp_path):
    import json

    selected = [
        {"steam_app_id": 10, "preference": None, "name": "Synthetic A", "page": 1},
        {"steam_app_id": 20, "preference": None, "name": "Synthetic B", "page": 1},
    ]
    signals = {
        "selected_games": selected,
        "write_permission_configured": True,
        "catalogue_guard": {"count": 100},
        "owned_games": 2,
        "library_last_synced_at": None,
        "profile_sha256": "unchanged",
        "affinity_profile_sha256": "unchanged-affinity",
    }
    inspect = Mock(return_value=signals)
    monkeypatch.setattr(launcher, "inspect_preference_signals", inspect)
    restore = Mock(return_value=True)
    monkeypatch.setattr(launcher, "restore_preferences", restore)

    def browser(*args, **kwargs):
        stage = kwargs["env"]["GAMEREC_PREFERENCE_STAGE"]
        directory = tmp_path / "frontend/live-results/preferences" / stage
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "stage.json").write_text(
            json.dumps(
                {
                    "stage": stage,
                    "games": [
                        {"steam_app_id": 100, "name": "Candidate", "hybrid_score": 0.5}
                    ],
                }
            )
        )
        return SimpleNamespace(returncode=0, stdout=f"Stage for {USER}\n", stderr="")

    run = Mock(side_effect=browser)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    return SimpleNamespace(
        root=tmp_path,
        signals=signals,
        inspect=inspect,
        restore=restore,
        run=run,
        browser=browser,
        directory=tmp_path / "frontend/live-results/preferences",
    )


def test_preferences_workflow_captures_stages_and_restores(
    launcher, preference_workflow, capsys
):
    import json

    state = preference_workflow
    assert launcher.run_preferences(state.root, "project", USER, {}, ["browser"]) == 0
    assert state.run.call_count == 4
    assert state.restore.call_count == 2
    assert state.restore.call_args.args[2] == [10, 20]
    assert [
        call.kwargs["env"]["GAMEREC_PREFERENCE_STAGE"]
        for call in state.run.call_args_list
    ] == [
        "baseline",
        "liked",
        "disliked",
        "restored",
    ]
    report = json.loads((state.directory / "comparison.json").read_text())
    assert report["profile_restored"] is True
    assert report["affinity_restored"] is True
    assert report["restored_top20_ids_match"] is True
    journal = json.loads((state.directory / "recovery.json").read_text())
    assert journal["restored"] is True
    assert USER not in (state.directory / "comparison.json").read_text()
    assert USER not in (state.directory / "recovery.json").read_text()
    assert USER not in capsys.readouterr().out


def test_preferences_browser_failure_still_runs_independent_cleanup(
    launcher, preference_workflow, capsys
):
    state = preference_workflow
    state.run.side_effect = [
        state.browser(env={"GAMEREC_PREFERENCE_STAGE": "baseline"}),
        SimpleNamespace(
            returncode=1, stdout=f"Failure /users/{USER}/preferences", stderr=""
        ),
    ]
    assert launcher.run_preferences(state.root, "project", USER, {}, ["browser"]) == 1
    state.restore.assert_called_once()
    assert USER not in capsys.readouterr().out


def test_preferences_interrupt_still_restores_original_absence(
    launcher, preference_workflow
):
    state = preference_workflow
    state.run.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        launcher.run_preferences(state.root, "project", USER, {}, ["browser"])
    state.restore.assert_called_once()


def test_preferences_final_cleanup_failure_is_not_reported_as_success(
    launcher, preference_workflow, capsys
):
    import json

    state = preference_workflow
    state.restore.side_effect = [True, False]
    assert launcher.run_preferences(state.root, "project", USER, {}, ["browser"]) == 1
    assert (
        json.loads((state.directory / "recovery.json").read_text())["restored"] is False
    )
    assert "RESTORATION FAILED" in capsys.readouterr().out


def test_preferences_unresolved_journal_blocks_new_writes(
    launcher, preference_workflow
):
    state = preference_workflow
    state.directory.mkdir(parents=True)
    (state.directory / "recovery.json").write_text('{"restored": false}')
    assert launcher.run_preferences(state.root, "project", USER, {}, ["browser"]) == 1
    state.inspect.assert_not_called()
    state.run.assert_not_called()
    state.restore.assert_not_called()


def test_preferences_refuses_existing_ratings(launcher, preference_workflow):
    state = preference_workflow
    state.signals["selected_games"][0]["preference"] = "liked"
    assert launcher.run_preferences(state.root, "project", USER, {}, ["browser"]) == 1
    state.run.assert_not_called()
    state.restore.assert_not_called()


def test_preferences_catalogue_changes_abort_and_cleanup(launcher, preference_workflow):
    state = preference_workflow
    changed = {**state.signals, "catalogue_guard": {"count": 101}}
    state.inspect.side_effect = [state.signals, changed]
    assert launcher.run_preferences(state.root, "project", USER, {}, ["browser"]) == 1
    state.restore.assert_called_once()


def test_preference_cleanup_never_places_identifier_in_command(
    launcher, monkeypatch, tmp_path
):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"restored":true}'))
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.restore_preferences(tmp_path, "project", [10, 20], USER) is True
    command = run.call_args.args[0]
    assert USER not in command
    assert command[-2:] == ["10", "20"]
    assert "sha256" in command[-4]


def test_live_selection_prefers_example_and_highest_playtime_without_overwriting_ratings():
    from gamerec.scripts.inspect_preference_signals import (
        OwnedPreferenceGame,
        select_unrated_games,
    )

    games = [
        OwnedPreferenceGame(10, 1, None, "Lowest ID"),
        OwnedPreferenceGame(20, 12000, None, "High playtime"),
        OwnedPreferenceGame(582010, 300, None, "Example"),
        OwnedPreferenceGame(30, 20000, "liked", "Already rated"),
    ]
    assert select_unrated_games(games) == [582010, 20]
    assert select_unrated_games(games[:2]) == [10, 20]
    with pytest.raises(ValueError, match="Two unrated"):
        select_unrated_games([games[0], games[-1]])


def test_preference_signal_fingerprint_is_key_order_independent():
    from gamerec.scripts.inspect_preference_signals import fingerprint

    assert fingerprint({"Action": 1, "RPG": -1}) == fingerprint(
        {"RPG": -1, "Action": 1}
    )
    assert fingerprint({"Action": 1}) != fingerprint({"Action": -1})


def test_preference_cleanup_attempts_both_deletes_after_one_network_failure(
    launcher, monkeypatch, tmp_path
):
    import io
    from contextlib import asynccontextmanager, redirect_stdout
    from unittest.mock import AsyncMock

    import httpx

    from gamerec import db as db_module
    from gamerec.core.config import settings

    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/10"):
            raise httpx.ConnectError("Synthetic cleanup failure")
        return httpx.Response(204)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    database = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=list))
    )

    @asynccontextmanager
    async def session():
        yield database

    disposed = AsyncMock()
    monkeypatch.setattr(db_module, "SessionLocal", session)
    monkeypatch.setattr(db_module, "engine", SimpleNamespace(dispose=disposed))
    monkeypatch.setattr(settings, "steamid64_test", USER)

    def execute_cleanup(command, **kwargs):
        code_index = command.index("-c")
        monkeypatch.setattr("sys.argv", ["-c", *command[code_index + 2 :]])
        output = io.StringIO()
        with redirect_stdout(output):
            exec(compile(command[code_index + 1], "<cleanup-test>", "exec"), {})  # noqa: S102 -- exercise the fixed local cleanup program
        return SimpleNamespace(returncode=0, stdout=output.getvalue())

    monkeypatch.setattr(launcher.subprocess, "run", execute_cleanup)
    assert launcher.restore_preferences(tmp_path, "project", [10, 20], USER) is False
    assert requests == [
        f"/users/{USER}/preferences/10",
        f"/users/{USER}/preferences/20",
    ]
    disposed.assert_awaited_once()
