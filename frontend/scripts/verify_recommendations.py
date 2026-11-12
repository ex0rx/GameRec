"""Run one live browser check using the configured ID without printing it."""

import argparse
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
    if args.library and len(steam_id) != 17:
        print("Library verification requires a 17-digit STEAMID64_TEST.")
        return 1

    lock = json.loads((root / "frontend/package-lock.json").read_text())
    version = lock["packages"]["node_modules/@playwright/test"]["version"]
    environment = os.environ.copy()
    environment["GAMEREC_TEST_STEAM_ID"] = steam_id
    environment["PLAYWRIGHT_BASE_URL"] = args.base_url
    result = subprocess.run(
        [
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
            "-v",
            f"{root / 'frontend'}:/app",
            "-v",
            f"{args.project_name}_frontend_node_modules:/app/node_modules",
            "-w",
            "/app",
            f"mcr.microsoft.com/playwright:v{version}-noble",
            "npm",
            "run",
            "test:library:live"
            if args.library
            else "test:recommendations-page:live"
            if args.page
            else "test:recommendations:live",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if args.library:
        redact_library_artifacts(root / "frontend/live-results/library", steam_id)
    # Playwright diagnostics may include supplied arguments on failures.
    print((result.stdout + result.stderr).replace(steam_id, "[redacted]"), end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
