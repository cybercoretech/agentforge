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

It also answers the three questions the Codex and OpenCode preflights ask (`--version`,
`exec --help`/`run --help`, and OpenCode's `debug agents --standalone`), with the same long flags a
real agent documents, so the conformance suite exercises each preflight instead of bypassing it
(ADR-0060, ADR-0061). It identifies as itself, not as Codex or OpenCode, so the probes that only a
real agent can answer (the sandbox runtime, the guard plugin, project isolation, the model catalog)
are not applied to it.
"""

import json
import os
import re
import sys
from pathlib import Path

MESSAGE_PATH = re.compile(r"(/\S*?/COMMIT_MSG)")

VERSION = "conformance-stub 1.0.0"

HELP = """Usage: conformance-stub exec [OPTIONS] [PROMPT]

  -c, --config <key=value>
  -m, --model <MODEL>
      --oss
      --local-provider <OSS_PROVIDER>
  -s, --sandbox <SANDBOX_MODE>  [possible values: read-only, workspace-write, danger-full-access]
  -C, --cd <DIR>
      --add-dir <DIR>
      --ephemeral
      --ignore-user-config
      --color <COLOR>
  -o, --output-last-message <FILE>
"""

RUN_HELP = """DESCRIPTION
  Run the stub agent with a message

USAGE
  conformance-stub run [flags] [<message...>]

FLAGS
      --agent <id>      Agent to use
      --format <choice> Output format (choices: default, json)
      --model <model>   Model to use
      --standalone      Run without the background service
"""


def answer_preflight(arguments):
    """Returns an exit code when the invocation is a preflight question, else None."""
    if arguments == ["--version"]:
        print(VERSION)
        return 0
    if arguments[:1] == ["exec"] and any(part in ("--help", "-h") for part in arguments[1:]):
        print(HELP)
        return 0
    if arguments[:1] == ["run"] and any(part in ("--help", "-h") for part in arguments[1:]):
        print(RUN_HELP)
        return 0
    if arguments[:1] == ["debug"] and arguments[1:2] == ["agents"]:
        print(json.dumps(configured_agents()))
        return 0
    return None


def configured_agents():
    """Echo the agents OpenCode would load from the config content the bridge passed."""
    try:
        document = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"))
    except json.JSONDecodeError:
        return []
    agents = document.get("agents") if isinstance(document, dict) else None
    if not isinstance(agents, dict):
        return []
    return [
        {"id": identifier, "mode": definition.get("mode")}
        for identifier, definition in agents.items()
        if isinstance(definition, dict)
    ]


def find_message_path(arguments, stdin_text):
    for haystack in (" ".join(arguments), stdin_text):
        match = MESSAGE_PATH.search(haystack)
        if match:
            return Path(match.group(1))
    return None


def main():
    arguments = sys.argv[1:]
    preflight = answer_preflight(arguments)
    if preflight is not None:
        return preflight
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
