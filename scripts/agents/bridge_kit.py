#!/usr/bin/env python3
"""Shared plumbing for AgentForge real-agent bridges (ADR-0058).

A bridge sits between the process adapter and a coding agent. It decodes the
`agentforge-task-prompt-v1` document from stdin, turns it into whatever the agent understands,
verifies every changed path against the task's allowed and forbidden paths, and commits the result
so the repository's pre-commit gate runs on it.

Everything that must behave identically for every agent lives here:

- the strict prompt codec, pinned to `fixtures/task-prompt-v1.txt` (also asserted by the Rust
  encoder in `crates/agentforge-adapter`);
- the path-boundary rules and changed-path discovery, including rename and copy sources;
- the isolated Cargo target directory for linked worktrees;
- the neutral MCP gateway invocation;
- the commit message and trailers;
- the exit-code contract.

A bridge supplies only what is genuinely agent-specific: its instruction text, its argv, its
permission or sandbox policy, how it renders MCP configuration, and its co-author trailer.

Exit codes, which every bridge must preserve:
  0 committed
  2 invalid input or configuration
  3 no changes
  4 path violation
  5 commit or pre-commit gate failure
  anything else is the agent's own exit status

Run the kit's own tests with `python3 scripts/agents/bridge_kit.py --self-test`. End-to-end
behavior, including the adversarial cases, is covered by `scripts/agents/bridge_conformance.py`.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_COMMITTED = 0
EXIT_INVALID = 2
EXIT_NO_CHANGES = 3
EXIT_PATH_VIOLATION = 4
EXIT_COMMIT_FAILED = 5

HEADER = b"agentforge-task-prompt-v1\n"
SCALARS = ["contract_version", "task_id", "milestone_id", "primary_role", "goal"]
LISTS = [
    "non_goals",
    "dependency_task_ids",
    "allowed_paths",
    "forbidden_paths",
    "capabilities",
    "required_approvals",
    "required_gates",
    "expected_outputs",
    "evidence_requirements",
]
MAX_SUMMARY_CHARS = 4000

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "task-prompt-v1.txt"


class PromptError(ValueError):
    """The stdin document is not a valid `agentforge-task-prompt-v1` prompt."""


class BridgeError(ValueError):
    """The bridge is misconfigured for this task."""


def decode_prompt(data):
    """Strictly decodes the length-delimited v1 document produced by render_task_prompt."""
    if not data.startswith(HEADER):
        raise PromptError("missing agentforge-task-prompt-v1 header")
    position = len(HEADER)

    def read_header(expected):
        nonlocal position
        end = data.find(b"\n", position)
        if end < 0:
            raise PromptError(f"truncated before {expected}")
        name, _, length = data[position:end].decode("utf-8").partition(" ")
        if name != expected or not length.isdigit():
            raise PromptError(f"expected field {expected!r}, found {name!r}")
        position = end + 1
        return int(length)

    def read_value(name):
        nonlocal position
        length = read_header(name)
        end = position + length
        if end + 1 > len(data) or data[end : end + 1] != b"\n":
            raise PromptError(f"truncated value for {name}")
        value = data[position:end].decode("utf-8")
        position = end + 1
        return value

    task = {}
    for name in SCALARS:
        task[name] = read_value(name)
    for name in LISTS:
        count = read_header(name)
        task[name] = [read_value("item") for _ in range(count)]
    if position != len(data):
        raise PromptError("trailing bytes after the last field")
    return task


def encode_prompt(task):
    """The inverse of `decode_prompt`, mirroring the Rust encoder."""
    output = bytearray(HEADER)

    def field(name, value):
        raw = value.encode("utf-8")
        output.extend(f"{name} {len(raw)}\n".encode())
        output.extend(raw + b"\n")

    for name in SCALARS:
        field(name, task[name])
    for name in LISTS:
        output.extend(f"{name} {len(task[name])}\n".encode())
        for value in task[name]:
            field("item", value)
    return bytes(output)


def within(path, roots):
    path = path.strip("/")
    for root in roots:
        root = root.strip("/")
        if root in ("", ".") or path == root or path.startswith(root + "/"):
            return True
    return False


def boundary_violations(paths, allowed, forbidden):
    return sorted(
        path for path in paths if not within(path, allowed) or (forbidden and within(path, forbidden))
    )


def runnable_paths(task):
    """Allowed paths whose files a `run_local_commands` task may execute directly.

    This is the neutral half of dogfooding finding 22: it decides which paths are runnable, while
    each bridge decides how to express that in its own permission language. Paths with spaces are
    skipped, because tool patterns are space-separated in every agent CLI we target.
    """
    if "run_local_commands" not in task["capabilities"]:
        return []
    paths = []
    for path in task["allowed_paths"]:
        path = path.strip().rstrip("/")
        while path.startswith("./"):
            path = path[2:]
        if not path or " " in path:
            continue
        paths.append(path)
    return paths


def git_output(*arguments):
    return subprocess.run(["git", *arguments], check=True, capture_output=True, text=True).stdout.strip()


def cargo_target_base():
    try:
        metadata = subprocess.run(
            ["cargo", "metadata", "--no-deps", "--offline", "--format-version", "1"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return json.loads(metadata).get("target_directory")


def worktree_target_dir(git_dir, common_dir, base, current=None):
    """The isolated Cargo target directory for a linked worktree, matching scripts/gate.sh.

    Returns None for the main checkout or when the caller already chose a target directory.
    """
    if current or git_dir == common_dir or not base:
        return None
    return str(Path(base) / "agentforge-worktrees" / Path(git_dir).name)


def agent_environment(label):
    """The agent's environment: inherited, plus an isolated CARGO_TARGET_DIR in linked worktrees,
    so builds the agent runs directly cannot collide with other checkouts (P2-M029)."""
    environment = dict(os.environ)
    target = worktree_target_dir(
        git_output("rev-parse", "--path-format=absolute", "--git-dir"),
        git_output("rev-parse", "--path-format=absolute", "--git-common-dir"),
        cargo_target_base(),
        environment.get("CARGO_TARGET_DIR"),
    )
    if target:
        environment["CARGO_TARGET_DIR"] = target
        print(f"{label}: agent CARGO_TARGET_DIR={target}", flush=True)
    return environment


def changed_paths():
    output = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        check=True,
        capture_output=True,
    ).stdout
    entries = output.split(b"\0")
    paths = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status, path = entry[:2].decode(), entry[3:].decode("utf-8")
        paths.append(path)
        if "R" in status or "C" in status:
            # Renames and copies are followed by the source path, which must also be in bounds.
            paths.append(entries[index].decode("utf-8"))
            index += 1
    return sorted(set(paths))


def bullets(values, empty="none"):
    return "\n".join(f"- {value}" for value in values) if values else f"- {empty}"


def project_root(marker=".forge/state/tasks.snapshot"):
    """The AgentForge project that owns this worktree, when it has task state (so tool calls are
    audited there); None for a remote worker's clone, which has none."""
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    root = Path(result.stdout.strip()).parent
    return root if (root / marker).is_file() else None


def mcp_server_arguments(task, forge, contract_path, worktree, root):
    """The neutral `forge mcp serve` argv for a task holding use_mcp_tools, or None.

    Each bridge renders this in its own configuration format.
    """
    if "use_mcp_tools" not in task["capabilities"]:
        return None
    if not forge:
        raise BridgeError(
            "the task holds use_mcp_tools but no forge executable was found; pass --forge PATH "
            "(profiles run with a cleared environment)"
        )
    serve = ["mcp", "serve", "--contract", str(contract_path), "--worktree", str(worktree)]
    if root:
        serve += ["--root", str(root)]
    return serve


def fallback_message(task, paths):
    subject_goal = task["goal"].splitlines()[0][:60].rstrip(".")
    return (
        f"feat(agent): {task['task_id']} {subject_goal}\n\n"
        f"The agent did not supply a commit message; this one was generated by the bridge.\n\n"
        f"Goal:\n{task['goal']}\n\nChanged files:\n{bullets(paths)}\n"
    )


def commit_message(task, paths, message_file, summary, agent_name, bridge_path, model=None, co_author=None):
    """The commit message: the agent's own, or a generated fallback, plus summary and trailers."""
    message = message_file.read_text(encoding="utf-8").strip() if message_file.exists() else ""
    if not message:
        message = fallback_message(task, paths).strip()
    trailer = [
        "",
        f"AgentForge-Task: {task['task_id']} (milestone {task['milestone_id']})",
        f"AgentForge-Agent: {agent_name} via {bridge_path}" + (f" (model {model})" if model else ""),
    ]
    if co_author:
        trailer.append(f"Co-Authored-By: {co_author}")
    if summary:
        message += "\n\nAgent summary:\n" + summary.strip()[-MAX_SUMMARY_CHARS:]
    message += "\n" + "\n".join(trailer) + "\n"
    return message


def commit(task, paths, message_file, summary, agent_name, bridge_path, model=None, co_author=None):
    """Stage only the verified paths and commit them, letting the repository's hook run."""
    message = commit_message(
        task, paths, message_file, summary, agent_name, bridge_path, model, co_author
    )
    subprocess.run(["git", "add", "--all", "--", *paths], check=True)
    result = subprocess.run(["git", "commit", "-F", "-"], input=message, text=True, capture_output=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def changed_paths_repository(paths, allowed, forbidden):
    """Convenience for tests: the violation list for a set of paths."""
    return boundary_violations(paths, allowed, forbidden)


def self_test():
    sample = {
        "contract_version": "1",
        "task_id": "P9-M001-T0001",
        "milestone_id": "P9-M001",
        "primary_role": "implementer",
        "goal": "Multi-line goal\nwith ünïcode ✓",
        "non_goals": ["no scope creep"],
        "dependency_task_ids": [],
        "allowed_paths": ["crates/example", "docs/EXAMPLE.md"],
        "forbidden_paths": ["crates/example/secret"],
        "capabilities": ["run_local_commands", "write_owned_paths"],
        "required_approvals": [],
        "required_gates": ["full"],
        "expected_outputs": ["tests", "docs"],
        "evidence_requirements": [],
    }

    # K2: the checked-in fixture is the contract with the Rust encoder (ADR-0058).
    fixture = FIXTURE.read_bytes()
    decoded = decode_prompt(fixture)
    assert decoded["task_id"] == "P6-M004-T0001", decoded["task_id"]
    assert decoded["milestone_id"] == "P6-M004", decoded["milestone_id"]
    assert decoded["goal"] == "Add the shared bridge kit\nand keep the exit codes stable (café ✓)"
    assert decoded["capabilities"] == ["write_owned_paths", "run_local_commands", "use_mcp_tools"]
    assert decode_prompt(encode_prompt(decoded)) == decoded, "fixture round trip"
    assert encode_prompt(decoded) == fixture, "re-encoding the fixture must be byte-identical"

    # K3: malformed prompts are refused, not guessed at.
    encoded = encode_prompt(sample)
    assert decode_prompt(encoded) == sample, "round trip"
    goal_header = f"goal {len(sample['goal'].encode())}\n".encode()
    assert goal_header in encoded
    for broken in [
        encoded[:-3],
        b"wrong-header\n" + encoded[len(HEADER) :],
        encoded.replace(goal_header, b"goal xx\n", 1),
        encoded.replace(goal_header, b"goal 999\n", 1),
        encoded.replace(b"task_id", b"task_ix", 1),
        encoded + b"extra",
    ]:
        try:
            decode_prompt(broken)
        except (PromptError, UnicodeDecodeError):
            continue
        raise AssertionError(f"accepted malformed prompt: {broken[:40]!r}")

    # K4: boundary rules, including the prefix trap and forbidden precedence.
    allowed, forbidden = sample["allowed_paths"], sample["forbidden_paths"]
    assert boundary_violations(["crates/example/src/lib.rs", "docs/EXAMPLE.md"], allowed, forbidden) == []
    assert boundary_violations(["crates/examples/x.rs"], allowed, forbidden) == ["crates/examples/x.rs"]
    assert boundary_violations(["README.md"], allowed, forbidden) == ["README.md"]
    assert boundary_violations(["crates/example/secret/key"], allowed, forbidden) == [
        "crates/example/secret/key"
    ]

    # Finding 22: a run_local_commands task may run files in its own allowed paths, and only those.
    scripted = dict(
        sample,
        allowed_paths=["scripts/tool", "./docs/X.md", "has space/x", "crates/example/", ".github/ci.yml"],
    )
    runnable = runnable_paths(scripted)
    assert runnable == ["scripts/tool", "docs/X.md", "crates/example", ".github/ci.yml"], runnable
    assert runnable_paths(dict(scripted, capabilities=["write_owned_paths"])) == [], "needs run_local_commands"
    assert boundary_violations(["anything/at/all"], ["."], []) == [], "a root of '.' allows everything"

    # K5: the commit contract.
    with tempfile.TemporaryDirectory() as scratch:
        message_file = Path(scratch) / "COMMIT_MSG"
        generated = commit_message(
            sample, ["a.rs"], message_file, "did the work", "Test Agent", "scripts/agents/test-bridge"
        )
        assert generated.startswith("feat(agent): P9-M001-T0001 Multi-line goal"), generated
        assert "The agent did not supply a commit message" in generated
        assert "AgentForge-Task: P9-M001-T0001 (milestone P9-M001)" in generated
        assert "AgentForge-Agent: Test Agent via scripts/agents/test-bridge\n" in generated
        assert "did the work" in generated
        assert generated.endswith("\n") and not generated.endswith("\n\n"), generated[-20:]
        assert "Co-Authored-By" not in generated, "no co-author unless the bridge asks for one"

        message_file.write_text("feat(x): agent wrote this\n\nbody\n", encoding="utf-8")
        explicit = commit_message(
            sample,
            ["a.rs"],
            message_file,
            "",
            "Test Agent",
            "scripts/agents/test-bridge",
            "some-model",
            "Test <test@example.com>",
        )
        assert explicit.startswith("feat(x): agent wrote this\n\nbody\n\nAgentForge-Task:"), explicit
        assert "AgentForge-Agent: Test Agent via scripts/agents/test-bridge (model some-model)" in explicit
        assert "Co-Authored-By: Test <test@example.com>" in explicit
        assert "Agent summary:" not in explicit

        long_summary = "x" * (MAX_SUMMARY_CHARS + 100)
        clipped = commit_message(
            sample, ["a.rs"], message_file, long_summary, "Test Agent", "b", None, None
        )
        assert long_summary not in clipped, "the summary is bounded"
        assert f"Agent summary:\n{'x' * MAX_SUMMARY_CHARS}" in clipped

    # MCP: the neutral argv, and the refusal when a task needs it without a forge binary.
    with tempfile.TemporaryDirectory() as scratch:
        contract = Path(scratch) / "contract.txt"
        assert mcp_server_arguments(sample, "/bin/forge", contract, "/wt", None) is None
        gated = dict(sample, capabilities=sample["capabilities"] + ["use_mcp_tools"])
        try:
            mcp_server_arguments(gated, None, contract, "/wt", None)
            raise AssertionError("use_mcp_tools without forge must refuse")
        except BridgeError:
            pass
        assert mcp_server_arguments(gated, "/bin/forge", contract, "/wt", "/project") == [
            "mcp",
            "serve",
            "--contract",
            str(contract),
            "--worktree",
            "/wt",
            "--root",
            "/project",
        ]
        assert mcp_server_arguments(gated, "/bin/forge", contract, "/wt", None) == [
            "mcp",
            "serve",
            "--contract",
            str(contract),
            "--worktree",
            "/wt",
        ]

    common = "/repo/.git"
    assert worktree_target_dir(common, common, "/target") is None, "main checkout keeps its target"
    assert (
        worktree_target_dir("/repo/.git/worktrees/P1-M007-T0003", common, "/target")
        == "/target/agentforge-worktrees/P1-M007-T0003"
    )
    assert worktree_target_dir("/repo/.git/worktrees/x", common, "/target", "/chosen") is None

    print("bridge-kit self-test passed")
    return EXIT_COMMITTED


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Shared plumbing for AgentForge agent bridges.")
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        return self_test()
    parser.print_help()
    return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
