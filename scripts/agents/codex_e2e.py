#!/usr/bin/env python3
"""Drive the real Codex CLI through the bridge, end to end (ADR-0060).

This is the harness behind the corrected record: it starts a stub OpenAI Responses API on the port
Codex's built-in Ollama provider uses, hands Codex a real task prompt through
`scripts/agents/codex-bridge`, and asserts the two outcomes that matter:

  pass     the model issues `exec_command`, Codex runs it inside its sandbox, the bridge commits the
           model's own message with the AgentForge trailers;
  refusal  the model writes outside the task boundary, the bridge exits 4, and nothing is committed.

The stub is not a model and this harness does not test model quality. It tests the bridge, the
sandbox, and the local-provider plumbing. With no Codex CLI installed it prints `SKIP` and exits 0,
so CI can run it without pretending.

Usage: python3 scripts/agents/codex_e2e.py [--codex PATH] [--keep]

Exit codes: 0 passed or skipped, 1 failed, 2 invalid usage.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BRIDGE = ROOT / "scripts" / "agents" / "codex-bridge"
PORT = 11434
MODEL = "stub-model"
COMMIT_MARKER = re.compile(r"(/\S*?/COMMIT_MSG)")

TASK = {
    "contract_version": "1",
    "task_id": "P6-M006-T0001",
    "milestone_id": "P6-M006",
    "primary_role": "implementer",
    "goal": "Write a generated document through the Codex sandbox.",
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
    """What the next model turn should do: write this path, then stop."""

    def __init__(self):
        self.write_path = None


def response_object(status, output):
    return {
        "id": "resp_stub",
        "object": "response",
        "created_at": 0,
        "status": status,
        "model": MODEL,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "previous_response_id": None,
        "reasoning": None,
        "store": False,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "top_p": None,
        "truncation": "disabled",
        "usage": None,
        "metadata": {},
    }


def usage():
    return {
        "input_tokens": 1,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 1,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 2,
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

    def open_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def event(self, name, payload):
        data = f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()
        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")

    def close_stream(self):
        self.wfile.write(b"0\r\n\r\n")

    def do_GET(self):
        if self.path.startswith("/api/version"):
            # Codex refuses Ollama older than 0.13.4; answer as a current server.
            self.send_json({"version": "0.20.0"})
        elif self.path.startswith("/api/tags"):
            self.send_json(
                {
                    "models": [
                        {
                            "name": MODEL,
                            "model": MODEL,
                            "modified_at": "2026-01-01T00:00:00Z",
                            "size": 1,
                            "digest": "0" * 64,
                            "details": {
                                "format": "gguf",
                                "family": "stub",
                                "families": ["stub"],
                                "parameter_size": "1B",
                                "quantization_level": "Q4",
                            },
                        }
                    ]
                }
            )
        elif self.path.startswith("/v1/models"):
            self.send_json(
                {"object": "list", "data": [{"id": MODEL, "object": "model", "created": 0, "owned_by": "stub"}]}
            )
        else:
            self.send_json({"error": "not found"}, status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"raw": raw.decode("utf-8", "replace")}

        if self.path.startswith("/api/show"):
            self.send_json(
                {
                    "model": MODEL,
                    "capabilities": ["completion", "tools"],
                    "details": {"format": "gguf", "family": "stub", "parameter_size": "1B"},
                    "model_info": {},
                }
            )
            return
        if self.path.startswith("/v1/responses"):
            self.handle_responses(body)
            return
        self.send_json({"error": "not found"}, status=404)

    def handle_responses(self, body):
        items = body.get("input") or []
        already_ran = any(
            isinstance(item, dict) and item.get("type") == "function_call_output" for item in items
        )
        write_path = self.server.state.write_path
        use_tool = write_path is not None and not already_ran
        self.open_stream()
        self.event(
            "response.created",
            {"type": "response.created", "response": response_object("in_progress", [])},
        )

        if use_tool:
            target = Path(write_path)
            command = (
                f"mkdir -p {shlex.quote(str(target.parent))} && "
                f"printf 'written by the stub model\\n' > {shlex.quote(str(target))}"
            )
            # A real model writes its commit message to the path the instructions name.
            match = COMMIT_MARKER.search(json.dumps(body))
            if match:
                message = (
                    "feat(stub): write a generated file\n\n"
                    "Written by the stub model through Codex's sandbox."
                )
                command += f" && printf '%s\\n' {shlex.quote(message)} > {shlex.quote(match.group(1))}"
            arguments = json.dumps({"cmd": command})
            call = {
                "type": "function_call",
                "id": "fc_stub_1",
                "call_id": "call_stub_1",
                "name": "exec_command",
                "arguments": "",
                "status": "in_progress",
            }
            self.event(
                "response.output_item.added",
                {"type": "response.output_item.added", "output_index": 0, "item": call},
            )
            self.event(
                "response.function_call_arguments.delta",
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_stub_1",
                    "output_index": 0,
                    "delta": arguments,
                },
            )
            self.event(
                "response.function_call_arguments.done",
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": "fc_stub_1",
                    "output_index": 0,
                    "arguments": arguments,
                },
            )
            done = dict(call, arguments=arguments, status="completed")
            self.event(
                "response.output_item.done",
                {"type": "response.output_item.done", "output_index": 0, "item": done},
            )
            final = response_object("completed", [done])
        else:
            said = "Stub model finished."
            message = {
                "type": "message",
                "id": "msg_stub_1",
                "role": "assistant",
                "status": "in_progress",
                "content": [],
            }
            self.event(
                "response.output_item.added",
                {"type": "response.output_item.added", "output_index": 0, "item": message},
            )
            self.event(
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_stub_1",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": said,
                },
            )
            part = {"type": "output_text", "text": said, "annotations": []}
            done = dict(message, status="completed", content=[part])
            self.event(
                "response.output_item.done",
                {"type": "response.output_item.done", "output_index": 0, "item": done},
            )
            final = response_object("completed", [done])

        final["usage"] = usage()
        self.event("response.completed", {"type": "response.completed", "response": final})
        self.close_stream()



def port_is_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def git(repository, *arguments, check=True):
    return subprocess.run(
        ["git", *arguments], cwd=repository, check=check, capture_output=True, text=True
    )


def commits(repository):
    return int(git(repository, "rev-list", "--count", "HEAD").stdout.strip())


def make_repository():
    directory = Path(tempfile.mkdtemp(prefix="agentforge-codex-e2e-"))
    git(directory, "init", "-q", "-b", "main")
    git(directory, "config", "user.name", "Codex E2E")
    git(directory, "config", "user.email", "e2e@example.invalid")
    git(directory, "config", "core.hooksPath", "")
    (directory / "README.md").write_text("# fixture\n", encoding="utf-8")
    (directory / "docs").mkdir()
    (directory / "docs" / "keep.md").write_text("keep\n", encoding="utf-8")
    git(directory, "add", "-A")
    git(directory, "commit", "-q", "-m", "fixture")
    return directory


def run_bridge(codex, repository, home, prompt):
    environment = {**os.environ, "CODEX_HOME": str(home)}
    return subprocess.run(
        [
            sys.executable,
            str(BRIDGE),
            "--codex",
            codex,
            "--local-provider",
            "ollama",
            "--model",
            MODEL,
        ],
        cwd=repository,
        input=prompt,
        capture_output=True,
        env=environment,
        timeout=300,
    )


def commit_case(codex, server, home):
    repository = make_repository()
    baseline = commits(repository)
    server.state.write_path = repository / "docs" / "generated.md"
    prompt = encode_task()
    result = run_bridge(codex, repository, home, prompt)
    expect(
        result.returncode == 0,
        f"commit case exited {result.returncode}: {result.stderr.decode()[-600:]}",
    )
    expect(commits(repository) == baseline + 1, "the bridge must make exactly one commit")
    expect(
        (repository / "docs" / "generated.md").read_text(encoding="utf-8").strip()
        == "written by the stub model",
        "the sandboxed tool call must write the expected file",
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
    expect("AgentForge-Agent: Codex CLI via scripts/agents/codex-bridge" in message, "the agent trailer is missing")
    expect("Agent summary:" in message, "the model's summary is missing")
    return repository


def refusal_case(codex, server, home):
    repository = make_repository()
    baseline = commits(repository)
    server.state.write_path = repository / "OUTSIDE.txt"
    result = run_bridge(codex, repository, home, encode_task())
    expect(
        result.returncode == 4,
        f"the path violation must exit 4, got {result.returncode}: {result.stderr.decode()[-600:]}",
    )
    expect(commits(repository) == baseline, "nothing may be committed")
    expect((repository / "OUTSIDE.txt").exists(), "the offending file is kept for review")
    expect(b"outside the task boundary" in result.stderr, "the refusal must name the boundary")
    return repository


def encode_task():
    sys.path.insert(0, str(ROOT / "scripts" / "agents"))
    import bridge_kit

    return bridge_kit.encode_prompt(TASK)


def main():
    parser = argparse.ArgumentParser(description="End-to-end Codex bridge harness.")
    parser.add_argument("--codex", default=None, help="Codex executable (default: codex on PATH)")
    parser.add_argument("--keep", action="store_true", help="keep the fixture repositories")
    arguments = parser.parse_args()

    codex = arguments.codex or shutil.which("codex")
    if not codex:
        print("codex-e2e: SKIP: no Codex CLI found (install it or pass --codex PATH)")
        return 0
    if not Path(codex).exists() and not shutil.which(codex):
        print(f"codex-e2e: {codex} does not exist", file=sys.stderr)
        return 2
    if port_is_open(PORT):
        print(
            f"codex-e2e: SKIP: 127.0.0.1:{PORT} is already in use; stop any local Ollama server "
            "first, because Codex's built-in provider cannot be pointed elsewhere"
        )
        return 0

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    server.state = State()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = Path(tempfile.mkdtemp(prefix="agentforge-codex-home-"))
    repositories = []
    try:
        print(f"codex-e2e: using {codex}")
        repositories.append(commit_case(codex, server, home))
        print("  ok   commit case: one commit, model message, both trailers")
        repositories.append(refusal_case(codex, server, home))
        print("  ok   refusal case: exit 4, nothing committed, file kept")
    except Failure as error:
        print(f"codex-e2e: FAIL: {error}", file=sys.stderr)
        return 1
    finally:
        server.shutdown()
        server.server_close()
        if not arguments.keep:
            for repository in repositories:
                shutil.rmtree(repository, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)
    print("codex-e2e: passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
