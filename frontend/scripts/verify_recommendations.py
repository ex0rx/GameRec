"""Run one live browser check using the configured ID without printing it."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def redact_library_artifacts(directory: Path, steam_id: str) -> None:
    """Remove the configured ID from Playwright's textual failure diagnostics."""
    for path in directory.rglob("*"):
        if (
            path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in {".md", ".txt", ".json", ".log", ".html", ".xml"}
        ):
            contents = path.read_text()
            if steam_id in contents:
                path.write_text(contents.replace(steam_id, "[redacted]"))


def api_command(project_name: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "exec",
        "-T",
        "api",
        "python",
    ]


def inspect_preference_signals(
    root: Path, project_name: str, app_ids: list[int] | None = None
) -> dict:
    command = api_command(project_name) + [
        "-m",
        "gamerec.scripts.inspect_preference_signals",
    ]
    if app_ids:
        command += ["--app-ids", ",".join(map(str, app_ids))]
    result = subprocess.run(
        command, cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError("Read-only preference diagnostics failed")
    return json.loads(result.stdout)


def restore_preferences(
    root: Path, project_name: str, app_ids: list[int], steam_id: str
) -> bool:
    # Only journaled, originally absent preferences may be cleared by this helper.
    code = """
import asyncio,hashlib,json,sys,httpx
from sqlalchemy import select
from gamerec.core.config import settings
from gamerec.db import SessionLocal,engine
from gamerec.models import UserGamePreference
async def restore():
    try:
        if hashlib.sha256((settings.steamid64_test or '').encode()).hexdigest()!=sys.argv[1]:
            print(json.dumps({'restored':False}))
            return
        ids=[int(value) for value in sys.argv[2:]]
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',trust_env=False,timeout=30) as client:
            statuses=[]
            for appid in ids:
                try:
                    response=await client.delete(f'/users/{settings.steamid64_test}/preferences/{appid}')
                    statuses.append(response.status_code)
                except httpx.HTTPError:
                    statuses.append(0)
        async with SessionLocal() as db:
            remaining=(await db.scalars(select(UserGamePreference.steam_app_id).where(
                UserGamePreference.steamid64==settings.steamid64_test,
                UserGamePreference.steam_app_id.in_(ids)))).all()
        print(json.dumps({'restored':all(status==204 for status in statuses) and not remaining}))
    finally:
        await engine.dispose()
try:
    asyncio.run(restore())
except Exception:
    print(json.dumps({'restored':False}))
"""
    try:
        result = subprocess.run(
            api_command(project_name)
            + [
                "-c",
                code,
                hashlib.sha256(steam_id.encode()).hexdigest(),
                *map(str, app_ids),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return (
            result.returncode == 0 and json.loads(result.stdout).get("restored") is True
        )
    except (ValueError, AttributeError, OSError):
        return False


def preference_comparison(stages: dict[str, dict]) -> dict:
    baseline, changed, restored = (
        stages[key] for key in ("baseline", "disliked", "restored")
    )
    before = {
        game["steam_app_id"]: (rank, game)
        for rank, game in enumerate(baseline["games"], 1)
    }
    after = {
        game["steam_app_id"]: (rank, game)
        for rank, game in enumerate(changed["games"], 1)
    }
    changes = []
    for appid in sorted(before.keys() | after.keys()):
        previous, current = before.get(appid), after.get(appid)
        changes.append(
            {
                "steam_app_id": appid,
                "name": (current or previous)[1]["name"],
                "baseline_rank": previous[0] if previous else None,
                "updated_rank": current[0] if current else None,
                "baseline_scores": previous[1] if previous else None,
                "updated_scores": current[1] if current else None,
            }
        )
    return {
        "stages": stages,
        "comparison": changes,
        "rank_absence_means": "Not returned in that stage's top 20; not a Qdrant recall assertion.",
        "profile_restored": baseline["signals"]["profile_sha256"]
        == restored["signals"]["profile_sha256"],
        "affinity_restored": baseline["signals"]["affinity_profile_sha256"]
        == restored["signals"]["affinity_profile_sha256"],
        "restored_top20_ids_match": [game["steam_app_id"] for game in baseline["games"]]
        == [game["steam_app_id"] for game in restored["games"]],
    }


def run_preferences(
    root: Path,
    project_name: str,
    steam_id: str,
    environment: dict[str, str],
    command: list[str],
) -> int:
    directory = root / "frontend/live-results/preferences"
    directory.mkdir(parents=True, exist_ok=True)
    journal = directory / "recovery.json"
    if journal.exists():
        try:
            previous = json.loads(journal.read_text())
        except (ValueError, OSError):
            previous = {}
        if previous.get("restored") is not True:
            print(
                "Resolve the existing preference recovery.json before another live write check."
            )
            return 1
    app_ids: list[int] = []
    stages: dict[str, dict] = {}
    restored = False
    status = 1
    try:
        initial = inspect_preference_signals(root, project_name)
        games = initial["selected_games"]
        if not initial.get("write_permission_configured"):
            print(
                "Enable development preference writes before this explicit live check."
            )
            return 1
        if len(games) != 2 or any(game["preference"] is not None for game in games):
            raise ValueError("Two originally unrated embedded owned games are required")
        selected = [game["steam_app_id"] for game in games]
        if len(set(selected)) != 2 or any(
            type(appid) is not int or appid <= 0 for appid in selected
        ):
            raise ValueError("Invalid public app IDs in selection")
        app_ids = selected
        journal.write_text(
            json.dumps(
                {
                    "account_sha256": hashlib.sha256(steam_id.encode()).hexdigest(),
                    "original_preferences": [
                        {"steam_app_id": appid, "preference": None} for appid in app_ids
                    ],
                    "restored": False,
                    "recovery": "With the same configured account, DELETE these two preference records and verify their absence.",
                },
                indent=2,
            )
            + "\n"
        )
        journal.chmod(0o600)
        environment = environment.copy()
        environment["GAMEREC_PREFERENCE_CASES"] = json.dumps(games)
        for stage in ("baseline", "liked", "disliked", "restored"):
            if stage == "restored":
                restored = restore_preferences(root, project_name, app_ids, steam_id)
                if not restored:
                    raise RuntimeError("Preference restoration failed")
            stage_environment = {**environment, "GAMEREC_PREFERENCE_STAGE": stage}
            result = subprocess.run(
                command,
                cwd=root,
                env=stage_environment,
                capture_output=True,
                text=True,
                check=False,
            )
            print(
                (result.stdout + result.stderr).replace(steam_id, "[redacted]"), end=""
            )
            if result.returncode:
                raise RuntimeError("Preference browser verification failed")
            browser = json.loads((directory / stage / "stage.json").read_text())
            signals = inspect_preference_signals(root, project_name, app_ids)
            if signals["catalogue_guard"] != initial["catalogue_guard"]:
                raise RuntimeError("Catalogue changed during preference comparison")
            if (
                signals["owned_games"] != initial["owned_games"]
                or signals["library_last_synced_at"]
                != initial["library_last_synced_at"]
            ):
                raise RuntimeError("Library changed during preference comparison")
            stages[stage] = {**browser, "signals": signals}
        report = preference_comparison(stages)
        (directory / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
        if not report["profile_restored"] or not report["affinity_restored"]:
            raise RuntimeError("Restored recommendation signals differ from baseline")
        print(
            "Preference persistence, HTTP recommendations and restored mathematical signals verified."
        )
        status = 0
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        print(f"Preference verification did not complete ({type(exc).__name__}).")
        status = 1
    finally:
        if app_ids:
            # This independent host cleanup also runs when a browser process fails.
            restored = restore_preferences(root, project_name, app_ids, steam_id)
            journal.write_text(
                json.dumps(
                    {
                        "account_sha256": hashlib.sha256(steam_id.encode()).hexdigest(),
                        "original_preferences": [
                            {"steam_app_id": appid, "preference": None}
                            for appid in app_ids
                        ],
                        "restored": restored,
                        "recovery": "DELETE the listed originally absent preferences for the configured account; then rerun read-only verification.",
                    },
                    indent=2,
                )
                + "\n"
            )
            journal.chmod(0o600)
            if not restored:
                status = 1
                print(
                    "RESTORATION FAILED. Keep recovery.json and clear the two listed test preferences before further testing."
                )
        redact_library_artifacts(directory, steam_id)
    return status


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default=root.name)
    parser.add_argument("--base-url", default="http://frontend:5173")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--page",
        action="store_true",
        help="Verify the Recommendations page and capture ID-free screenshots.",
    )
    mode.add_argument(
        "--library",
        action="store_true",
        help="Verify the stored library page with masked screenshots and redacted diagnostics.",
    )
    mode.add_argument(
        "--library-sync",
        action="store_true",
        help="Explicitly sync the allowlisted library once, then verify cooldown and persisted data.",
    )
    mode.add_argument(
        "--preferences",
        action="store_true",
        help="Explicitly compare temporary library preferences and restore the original unrated state.",
    )
    args = parser.parse_args()
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            args.project_name,
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            "from gamerec.core.config import settings; print(settings.steamid64_test or '')",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        print(
            "Could not read development configuration from the running API container."
        )
        return 1
    steam_id = result.stdout.strip()
    if (
        not steam_id.isascii()
        or not steam_id.isdecimal()
        or not 0 < int(steam_id) < 2**64
        or steam_id != str(int(steam_id))
    ):
        print("Configure a valid STEAMID64_TEST locally before live verification.")
        return 1
    if (args.library or args.library_sync or args.preferences) and len(steam_id) != 17:
        print("Library verification requires a 17-digit STEAMID64_TEST.")
        return 1

    lock = json.loads((root / "frontend/package-lock.json").read_text())
    version = lock["packages"]["node_modules/@playwright/test"]["version"]
    environment = os.environ.copy()
    environment["GAMEREC_TEST_STEAM_ID"] = steam_id
    environment["PLAYWRIGHT_BASE_URL"] = args.base_url
    command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--shm-size=1gb",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--network",
        f"{args.project_name}_default",
        "-e",
        "GAMEREC_TEST_STEAM_ID",
        "-e",
        "PLAYWRIGHT_BASE_URL",
        *(
            ["-e", "GAMEREC_PREFERENCE_CASES", "-e", "GAMEREC_PREFERENCE_STAGE"]
            if args.preferences
            else []
        ),
        "-v",
        f"{root / 'frontend'}:/app",
        "-v",
        f"{args.project_name}_frontend_node_modules:/app/node_modules",
        "-w",
        "/app",
        f"mcr.microsoft.com/playwright:v{version}-noble",
        "npm",
        "run",
        "test:preferences:live"
        if args.preferences
        else "test:library-sync:live"
        if args.library_sync
        else "test:library:live"
        if args.library
        else "test:recommendations-page:live"
        if args.page
        else "test:recommendations:live",
    ]
    if args.preferences:
        return run_preferences(root, args.project_name, steam_id, environment, command)
    result = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if args.library:
        redact_library_artifacts(root / "frontend/live-results/library", steam_id)
    if args.library_sync:
        redact_library_artifacts(root / "frontend/live-results/library-sync", steam_id)
    # Playwright diagnostics may include supplied arguments on failures.
    print((result.stdout + result.stderr).replace(steam_id, "[redacted]"), end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
