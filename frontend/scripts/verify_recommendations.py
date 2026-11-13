"""Retired ID-only live launcher; protected APIs require a browser session."""

import argparse

SESSION_REQUIRED = (
    "This ID-only live launcher is retired. Library, preferences, sync and "
    "recommendations require an authenticated owner session. Use the signed-in "
    "browser verification in README; do not export session cookies. "
    "Session-aware frontend automation is deferred to Phase 2C."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", help="Retired compatibility argument.")
    parser.add_argument("--base-url", help="Retired compatibility argument.")
    mode = parser.add_mutually_exclusive_group()
    for option in ("--page", "--library", "--library-sync", "--preferences"):
        mode.add_argument(
            option, action="store_true", help="Requires a signed-in browser."
        )
    parser.parse_args()
    # Exit before reading configuration, spawning a browser or writing recovery data.
    print(SESSION_REQUIRED)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
