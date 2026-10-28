#!/usr/bin/env python3
"""Drive the real OpenCode CLI through the bridge, end to end (ADR-0061).

This harness starts a streaming OpenAI-compatible chat-completions stub on an ephemeral port,
writes a throwaway XDG config that registers it as the `stub` provider, and hands a real task prompt
to `scripts/agents/opencode-bridge`. It asserts the four outcomes the milestone depends on:

  commit    the model writes the expected file and its own commit message, and the bridge commits
            exactly once with both AgentForge trailers;
  refusal   the model writes outside the task boundary, the bridge exits 4, and nothing is
            committed (the offending file is kept for review);
  guard     the model calls `opencode_worktree_remove` with `force: true` against the linked
            worktree it is running in; the AgentForge guard has removed that tool, the call fails,
            the run makes no changes, and the worktree survives;
  failure   the provider rejects the request, the bridge exits 1 with one actionable hint, and
            nothing is committed.

The stub is not a model and this harness does not test model quality. It tests the bridge, the
policy, the guard, and the provider plumbing. With no OpenCode CLI installed it prints `SKIP` and
exits 0, so CI can run it without pretending.

Usage: python3 scripts/agents/opencode_e2e.py [--opencode PATH] [--keep]

Exit codes: 0 passed or skipped, 1 failed, 2 invalid usage.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BRIDGE = ROOT / "scripts" / "agents" / "opencode-bridge"
PROVIDER = "stub"
MODEL = "stub-model"
FULL_MODEL = f"{PROVIDER}/{MODEL}"
COMMIT_MARKER = re.compile(r"(/\S*?/COMMIT_MSG)")

TASK = {
    "contract_version": "1",
    "task_id": "P6-M007-T0001",
    "milestone_id": "P6-M007",
    "primary_role": "implementer",
    "goal": "Write a generated document through the OpenCode policy.",
    "non_goals": [],
    "dependency_task_ids": [],
    "allowed_paths": ["docs"],
    "forbidden_paths": ["docs/keep.md"],
    "capabilities": ["write_owned_paths"],
    "required_approvals": [],
    "required_gates": [],
    "expected_outputs": ["docs/generated.md"],
    "evidence_requirements": [],
}


class Failure(AssertionError):
    pass


def expect(condition, message):
    if not condition:
        raise Failure(message)


class State:
    """What the stub provider should do on the next model turn."""

    def __init__(self):
        self.mode = "write"
        self.path = None
        self.message_written = False


def chunk_payload(delta, finish_reason=None):
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def tool_call(name, arguments, identifier="call_stub_1"):
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "index": 0,
                "id": identifier,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def begin(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def chunk(self, payload):
        data = f"data: {json.dumps(payload)}\n\n".encode()
        self.write(data)

    def write(self, data):
        """A client that has what it needs may close the stream first; that is not a failure."""
        try:
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def finish(self):
        done = b"data: [DONE]\n\n"
        self.write(done)
        try:
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self.send_json({"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self.send_json({"error": "not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        if not self.path.startswith("/v1/chat/completions"):
            self.send_json({"error": "not found"}, status=404)
            return
        state = self.server.state
        self.server.requests += 1
        if state.mode == "fail":
            self.send_json(
                {
                    "error": {
                        "message": "Unauthorized: invalid api key",
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    }
                },
                status=401,
            )
            return
        self.stream(body, state)

    def stream(self, body, state):
        messages = body.get("messages") or []
        system = " ".join(
            str(message.get("content", "")) for message in messages if message.get("role") == "system"
        )
        if "You name conversations" in system:
            # The bridge disables the title agent; answer anyway so a regression cannot hang.
            self.begin()
            self.chunk(chunk_payload({"role": "assistant", "content": "STUB-TITLE"}))
            self.chunk(chunk_payload({}, "stop"))
            self.finish()
            return
        results = [message for message in messages if message.get("role") == "tool"]
        message_path = COMMIT_MARKER.search(json.dumps(messages))
        self.begin()

        call = None
        if state.mode == "write":
            if not results:
                call = tool_call("write", {"path": state.path, "content": "written by the stub model\n"})
            elif message_path and not state.message_written:
                state.message_written = True
                call = tool_call(
                    "write",
                    {
                        "path": message_path.group(1),
                        "content": "feat(stub): write a generated file\n\n"
                        "Written by the stub model through the OpenCode policy.\n",
                    },
                    identifier="call_stub_2",
                )
        elif state.mode == "outside":
            if not results:
                call = tool_call("write", {"path": state.path, "content": "outside\n"})
        elif state.mode == "guard":
            if not results:
                call = tool_call("opencode_worktree_remove", {"directory": state.path, "force": True})

        if call is not None:
            self.chunk(chunk_payload(call))
            self.chunk(chunk_payload({}, "tool_calls"))
        else:
            self.chunk(chunk_payload({"role": "assistant", "content": "Stub model finished."}))
            self.chunk(
                chunk_payload(
                    {},
                    "stop",
                )
            )
        self.finish()


def git(repository, *arguments, check=True):
    return subprocess.run(
        ["git", *arguments], cwd=repository, check=check, capture_output=True, text=True
    )


def commits(repository):
    return int(git(repository, "rev-list", "--count", "HEAD").stdout.strip())


def make_repository(prefix="agentforge-opencode-e2e-"):
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    git(directory, "init", "-q", "-b", "main")
    git(directory, "config", "user.name", "OpenCode E2E")
    git(directory, "config", "user.email", "e2e@example.invalid")
    git(directory, "config", "core.hooksPath", "")
    (directory / "README.md").write_text("# fixture\n", encoding="utf-8")
    (directory / "docs").mkdir()
    (directory / "docs" / "keep.md").write_text("keep\n", encoding="utf-8")
    git(directory, "add", "-A")
    git(directory, "commit", "-q", "-m", "fixture")
    return directory


def make_linked_worktree():
    main = make_repository("agentforge-opencode-main-")
    linked = Path(tempfile.mkdtemp(prefix="agentforge-opencode-linked-"))
    linked.rmdir()
    git(main, "worktree", "add", "-q", str(linked), "-b", "task")
    return main, linked


def write_config(home, port):
    """The provider the stub serves. The bridge supplies the policy; this only supplies the model."""
    directory = home / "config" / "opencode"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "opencode.json").write_text(
        json.dumps(
            {
                "providers": {
                    PROVIDER: {
                        "name": "Stub",
                        "env": ["STUB_API_KEY"],
                        "package": "@opencode/ai/providers/openai-compatible",
                        "settings": {"baseURL": f"http://127.0.0.1:{port}/v1"},
                        "models": {
                            MODEL: {
                                "name": "Stub Model",
                                "limit": {"context": 100000, "output": 4000},
                            }
                        },
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )


def run_bridge(opencode, repository, home, prompt, extra=()):
    environment = {
        **os.environ,
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "STUB_API_KEY": "stub-key",
    }
    return subprocess.run(
        [
            sys.executable,
            str(BRIDGE),
            "--opencode",
            opencode,
            "--model",
            FULL_MODEL,
            *extra,
        ],
        cwd=repository,
        input=prompt,
        capture_output=True,
        env=environment,
        timeout=600,
    )


def encode_task():
    sys.path.insert(0, str(ROOT / "scripts" / "agents"))
    import bridge_kit

    return bridge_kit.encode_prompt(TASK)


def commit_case(opencode, server, home):
    repository = make_repository()
    baseline = commits(repository)
    server.state.mode = "write"
    server.state.path = "docs/generated.md"
    result = run_bridge(opencode, repository, home, encode_task())
    expect(
        result.returncode == 0,
        f"commit case exited {result.returncode}: {result.stderr.decode()[-800:]}",
    )
    expect(commits(repository) == baseline + 1, "the bridge must make exactly one commit")
    expect(
        (repository / "docs" / "generated.md").read_text(encoding="utf-8").strip()
        == "written by the stub model",
        "the policy-allowed tool call must write the expected file",
    )
    message = git(repository, "log", "-1", "--format=%B").stdout
    expect(
        message.startswith("feat(stub): write a generated file"),
        f"the model's own commit message must be used, got {message.splitlines()[0]!r}",
    )
    expect(
        f"AgentForge-Task: {TASK['task_id']} (milestone {TASK['milestone_id']})" in message,
        "the task trailer is missing",
    )
    expect(
        "AgentForge-Agent: OpenCode via scripts/agents/opencode-bridge" in message,
        "the agent trailer is missing",
    )
    expect("Agent summary:" in message, "the model's summary is missing")
    expect(
        b"preflight:" in result.stdout,
        "the preflight must have run before the agent",
    )
    return repository


def refusal_case(opencode, server, home):
    repository = make_repository()
    baseline = commits(repository)
    server.state.mode = "outside"
    server.state.path = str(repository / "OUTSIDE.txt")
    result = run_bridge(opencode, repository, home, encode_task())
    expect(
        result.returncode == 4,
        f"the path violation must exit 4, got {result.returncode}: {result.stderr.decode()[-800:]}",
    )
    expect(commits(repository) == baseline, "nothing may be committed")
    expect((repository / "OUTSIDE.txt").exists(), "the offending file is kept for review")
    expect(b"outside the task boundary" in result.stderr, "the refusal must name the boundary")
    return repository


def guard_case(opencode, server, home):
    main, linked = make_linked_worktree()
    server.state.mode = "guard"
    server.state.path = str(linked)
    result = run_bridge(opencode, linked, home, encode_task())
    expect(
        result.returncode == 3,
        f"a guarded run with no changes must exit 3, got {result.returncode}: "
        f"{result.stderr.decode()[-800:]}",
    )
    expect(linked.is_dir(), "the guard must leave the active linked worktree in place")
    expect((linked / "README.md").is_file(), "the linked worktree must still be a checkout")
    listing = git(main, "worktree", "list").stdout
    expect(str(linked) in listing, "git must still list the linked worktree")
    combined = (result.stdout + result.stderr).decode("utf-8", "replace")
    expect(
        "opencode_worktree_remove" in combined and "No tool named" in combined,
        "the guarded call must be reported as an unavailable tool",
    )
    return main, linked


def failure_case(opencode, server, home):
    repository = make_repository()
    baseline = commits(repository)
    server.state.mode = "fail"
    result = run_bridge(opencode, repository, home, encode_task())
    expect(
        result.returncode == 1,
        f"a rejected provider request must exit 1, got {result.returncode}: "
        f"{result.stderr.decode()[-800:]}",
    )
    expect(commits(repository) == baseline, "nothing may be committed")
    expect(b"opencode-bridge: hint:" in result.stderr, "one actionable hint must be printed")
    expect(b"credentials" in result.stderr, "the hint must name the credential remedy")
    return repository


def main():
    parser = argparse.ArgumentParser(description="End-to-end OpenCode bridge harness.")
    parser.add_argument("--opencode", default=None, help="OpenCode executable (default: opencode on PATH)")
    parser.add_argument("--keep", action="store_true", help="keep the fixture repositories")
    arguments = parser.parse_args()

    opencode = arguments.opencode or shutil.which("opencode")
    if not opencode:
        print("opencode-e2e: SKIP: no OpenCode CLI found (install it or pass --opencode PATH)")
        return 0
    if not Path(opencode).exists() and not shutil.which(opencode):
        print(f"opencode-e2e: {opencode} does not exist", file=sys.stderr)
        return 2

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.state = State()
    server.requests = 0
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = Path(tempfile.mkdtemp(prefix="agentforge-opencode-home-"))
    created = []
    try:
        write_config(home, port)
        print(f"opencode-e2e: using {opencode} with the stub provider on 127.0.0.1:{port}")
        created.append(commit_case(opencode, server, home))
        print("  ok   commit case: one commit, model message, both trailers")
        created.append(refusal_case(opencode, server, home))
        print("  ok   refusal case: exit 4, nothing committed, file kept")
        main_repository, linked = guard_case(opencode, server, home)
        created.extend([main_repository, linked])
        print("  ok   guard case: forced worktree removal fails, the worktree survives")
        created.append(failure_case(opencode, server, home))
        print("  ok   failure case: exit 1, one credential hint, nothing committed")
    except Failure as error:
        print(f"opencode-e2e: FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        server.shutdown()
        server.server_close()
        if not arguments.keep:
            for repository in created:
                if repository and Path(repository).exists():
                    shutil.rmtree(repository, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)
    print("opencode-e2e: passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
