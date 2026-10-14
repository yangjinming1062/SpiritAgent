import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys

from .version import __version__


def main() -> None:
    parser = argparse.ArgumentParser(description="SpiritAgent Runner")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--check", action="store_true", help="Validate installed imports and static tool definitions")
    parser.add_argument("--desktop-endpoint", help="Desktop IPC path (named pipe or Unix socket)")
    args = parser.parse_args()
    if sys.platform not in {"win32", "darwin"}:
        parser.error("Runner supports Windows and macOS only")
    from .bootstrap import RunnerApplication

    application = RunnerApplication()
    if args.check:
        print(json.dumps({"version": __version__, "tool_count": application.check()}))
        return
    if not args.desktop_endpoint:
        parser.error("--desktop-endpoint is required")
    if not (token := os.environ.get("SPIRITAGENT_DESKTOP_TOKEN")):
        parser.error("SPIRITAGENT_DESKTOP_TOKEN is required")
    from .infrastructure.job_object import init_runner_job_object
    from .transport.desktop import DesktopEndpoint

    init_runner_job_object()
    logging.basicConfig(level=logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(application.run(DesktopEndpoint(path=args.desktop_endpoint, token=token)))


if __name__ == "__main__":
    main()
