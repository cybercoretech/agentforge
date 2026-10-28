#!/usr/bin/env python3
"""A stub coding agent for the bridge conformance suite (ADR-0058).

The bridges invoke their agent with a single executable path, so this file is executable and
behaves according to environment variables:

  STUB_MODE=modify | no_message | rename | fail | noop
  STUB_PATH              path to write for modify and no_message
  STUB_RENAME_FROM       source path for rename
  STUB_RENAME_TO         destination path for rename
  STUB_EXIT              status for fail (default 1)
  STUB_MESSAGE           commit message text (default "stub wrote this")

It also models the honest behavior of a real agent: it writes its commit message to the scratch
path the bridge told it about, which it finds by scanning its arguments and stdin.
"""

import os
import re
import sys
from pathlib import Path

MESSAGE_PATH = re.compile(r"(/\S*?/COMMIT_MSG)")


def find_message_path(arguments, stdin_text):
    for haystack in (" ".join(arguments), stdin_text):
        match = MESSAGE_PATH.search(haystack)
        if match:
            return Path(match.group(1))
    return None


def main():
    arguments = sys.argv[1:]
    stdin_text = ""
    if not sys.stdin.isatty():
        try:
            stdin_text = sys.stdin.read()
        except (OSError, UnicodeDecodeError):
            stdin_text = ""

    mode = os.environ.get("STUB_MODE", "noop")
    if mode == "noop":
        return 0
    if mode == "fail":
        return int(os.environ.get("STUB_EXIT", "1"))

    message_path = find_message_path(arguments, stdin_text)
    message = os.environ.get("STUB_MESSAGE", "stub wrote this")

    if mode == "rename":
        source = os.environ["STUB_RENAME_FROM"]
        destination = os.environ["STUB_RENAME_TO"]
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, destination)
    else:
        path = Path(os.environ["STUB_PATH"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("written by the stub\n", encoding="utf-8")

    if mode != "no_message" and message_path is not None:
        message_path.write_text(message + "\n\nwhy: conformance\n", encoding="utf-8")

    print("stub agent finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
