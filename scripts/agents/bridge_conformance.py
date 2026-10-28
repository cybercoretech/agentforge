#!/usr/bin/env python3
"""End-to-end conformance suite for AgentForge real-agent bridges (ADR-0058).

Each registered bridge is driven through the same cases against a stub agent inside a throwaway Git
repository:

  K6  in-boundary work              commits, with the agent's message and both trailers
  K7  out-of-boundary write         exit 4, nothing committed, the file is left for review
  K8  forbidden path                exit 4, nothing committed
  K9  rename into a forbidden path  exit 4, nothing committed (the source side is checked)
  K9b rename out of bounds          exit 4, nothing committed
  K10 agent failure                 the agent's own exit status, nothing committed
  K11 no changes                    exit 3, nothing committed
  K12 missing commit message        exit 0, generated fallback subject and trailers
  K13 failing pre-commit hook       exit 5, nothing committed, worktree preserved
  K14 dry run                       exit 0, the command is printed, nothing runs
  K15 malformed prompt              exit 2 before the agent runs

Adding a bridge means adding a registry entry, not new cases. A bridge that does not pass here is
not first-party.

Usage:
  python3 scripts/agents/bridge_conformance.py [--bridge NAME]... [--list] [-v]

Exit codes: 0 all cases passed, 1 at least one failed, 2 invalid usage.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STUB = ROOT / "scripts" / "agents" / "conformance_stub.py"

COMMIT_MESSAGE = re.compile(r"(/\S*?/COMMIT_MSG)")

# The task every case is given. Deliberately narrow, with one forbidden path inside an allowed one.
TASK = {
    "contract_version": "1",
    "task_id": "P6-M004-T0001",
    "milestone_id": "P6-M004",
    "primary_role": "implementer",
    "goal": "Conformance case\nfor the shared bridge kit",
    "non_goals": ["no new bridge"],
    "dependency_task_ids": [],
    "allowed_paths": ["writable"],
    "forbidden_paths": ["writable/secret"],
    "capabilities": ["write_owned_paths"],
    "required_approvals": [],
    "required_gates": [],
    "expected_outputs": ["a commit"],
    "evidence_requirements": [],
}

BRIDGES = [
    {
        "name": "claude-code",
        "path": ROOT / "scripts" / "agents" / "claude-code-bridge",
        "agent_flag": "--claude",
        "extra_args": [],
    },
    # P6-M005 adds Codex and P6-M006 adds OpenCode here. Each bridge must pass unchanged.
]


class CaseFailure(AssertionError):
    pass


def git(repo, *arguments, check=True):
    return subprocess.run(
        ["git", *arguments], cwd=repo, check=check, capture_output=True, text=True
    )


def commits(repo):
    return int(git(repo, "rev-list", "--count", "HEAD").stdout.strip())


def make_repository():
    """A throwaway repository with explicit identity and no hooks."""
    directory = tempfile.mkdtemp(prefix="agentforge-conformance-")
    repo = Path(directory)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Conformance")
    git(repo, "config", "user.email", "conformance@example.invalid")
    git(repo, "config", "core.hooksPath", "")
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    (repo / "writable").mkdir()
    (repo / "writable" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (repo / "writable" / "secret").mkdir()
    (repo / "writable" / "secret" / "key.txt").write_text("secret\n", encoding="utf-8")
    (repo / "writable" / "move-me.txt").write_text("move\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    return repo


def run_bridge(bridge, repo, prompt=None, extra=(), environment=None):
    command = [
        sys.executable,
        str(bridge["path"]),
        bridge["agent_flag"],
        str(STUB),
        *bridge["extra_args"],
        *extra,
    ]
    environment = {**os.environ, **(environment or {})}
    return subprocess.run(
        command,
        cwd=repo,
        input=prompt,
        capture_output=True,
        env=environment,
    )


def prompt_for(encode_prompt, task=None):
    return encode_prompt(task or TASK)


def case(name, bridge, body, encode_prompt):
    """Runs one case and reports it. Returns True on success."""
    repo = make_repository()
    baseline = commits(repo)
    try:
        body(repo, baseline)
    except CaseFailure as error:
        print(f"  FAIL {bridge['name']} :: {name}: {error}")
        return False
    except Exception as error:  # noqa: BLE001 - a crashing case is a failing case
        print(f"  FAIL {bridge['name']} :: {name}: unexpected {type(error).__name__}: {error}")
        return False
    finally:
        subprocess.run(["rm", "-rf", str(repo)], check=False)
    print(f"  ok   {bridge['name']} :: {name}")
    return True


def expect(condition, message):
    if not condition:
        raise CaseFailure(message)


def commit_fixture(bridge, encode_prompt):
    """Returns the case table for one bridge."""

    def prompt():
        return prompt_for(encode_prompt)

    def in_boundary(repo, baseline):
        result = run_bridge(
            bridge, repo, prompt(), environment={"STUB_MODE": "modify", "STUB_PATH": "writable/new.txt"}
        )
        expect(result.returncode == 0, f"exit {result.returncode}: {result.stderr.decode()[-400:]}")
        expect(commits(repo) == baseline + 1, "expected exactly one commit")
        message = git(repo, "log", "-1", "--format=%B").stdout
        expect("stub wrote this" in message, "the agent's own message must be used")
        expect(f"AgentForge-Task: {TASK['task_id']} (milestone {TASK['milestone_id']})" in message, "task trailer")
        expect("AgentForge-Agent: " in message, "agent trailer")

    def outside(repo, baseline):
        result = run_bridge(
            bridge, repo, prompt(), environment={"STUB_MODE": "modify", "STUB_PATH": "OUTSIDE.txt"}
        )
        expect(result.returncode == 4, f"expected exit 4, got {result.returncode}")
        expect(commits(repo) == baseline, "nothing may be committed")
        expect((repo / "OUTSIDE.txt").exists(), "the offending file is left for review, not reverted")

    def forbidden(repo, baseline):
        result = run_bridge(
            bridge,
            repo,
            prompt(),
            environment={"STUB_MODE": "modify", "STUB_PATH": "writable/secret/added.txt"},
        )
        expect(result.returncode == 4, f"expected exit 4, got {result.returncode}")
        expect(commits(repo) == baseline, "nothing may be committed")

    def rename_into_forbidden(repo, baseline):
        # Deleting a forbidden file by renaming it into bounds must still be refused.
        result = run_bridge(
            bridge,
            repo,
            prompt(),
            environment={
                "STUB_MODE": "rename",
                "STUB_RENAME_FROM": "writable/secret/key.txt",
                "STUB_RENAME_TO": "writable/stolen.txt",
            },
        )
        expect(result.returncode == 4, f"expected exit 4, got {result.returncode}")
        expect(commits(repo) == baseline, "nothing may be committed")

    def rename_out_of_bounds(repo, baseline):
        result = run_bridge(
            bridge,
            repo,
            prompt(),
            environment={
                "STUB_MODE": "rename",
                "STUB_RENAME_FROM": "writable/move-me.txt",
                "STUB_RENAME_TO": "MOVED.txt",
            },
        )
        expect(result.returncode == 4, f"expected exit 4, got {result.returncode}")
        expect(commits(repo) == baseline, "nothing may be committed")

    def agent_failure(repo, baseline):
        result = run_bridge(
            bridge, repo, prompt(), environment={"STUB_MODE": "fail", "STUB_EXIT": "7"}
        )
        expect(result.returncode == 7, f"the agent's status must propagate, got {result.returncode}")
        expect(commits(repo) == baseline, "nothing may be committed")

    def no_changes(repo, baseline):
        result = run_bridge(bridge, repo, prompt(), environment={"STUB_MODE": "noop"})
        expect(result.returncode == 3, f"expected exit 3, got {result.returncode}")
        expect(commits(repo) == baseline, "nothing may be committed")

    def missing_message(repo, baseline):
        result = run_bridge(
            bridge,
            repo,
            prompt(),
            environment={"STUB_MODE": "no_message", "STUB_PATH": "writable/new.txt"},
        )
        expect(result.returncode == 0, f"exit {result.returncode}: {result.stderr.decode()[-400:]}")
        expect(commits(repo) == baseline + 1, "expected one commit")
        message = git(repo, "log", "-1", "--format=%B").stdout
        expect(
            message.startswith("feat(agent): P6-M004-T0001"),
            f"expected a generated fallback subject, got {message.splitlines()[0]!r}",
        )
        expect("generated by the bridge" in message, "the fallback must say so")
        expect("AgentForge-Task: P6-M004-T0001" in message, "task trailer")

    def failing_hook(repo, baseline):
        hooks = repo / ".git" / "conformance-hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\necho 'gate failed' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        git(repo, "config", "core.hooksPath", str(hooks))
        result = run_bridge(
            bridge, repo, prompt(), environment={"STUB_MODE": "modify", "STUB_PATH": "writable/new.txt"}
        )
        expect(result.returncode == 5, f"expected exit 5, got {result.returncode}")
        expect(commits(repo) == baseline, "a failing pre-commit hook must not produce a commit")
        expect((repo / "writable" / "new.txt").exists(), "the worktree is preserved")

    def dry_run(repo, baseline):
        result = run_bridge(bridge, repo, prompt(), extra=["--dry-run"])
        expect(result.returncode == 0, f"expected exit 0, got {result.returncode}")
        expect(b"command:" in result.stdout, "the command must be printed")
        expect(commits(repo) == baseline, "nothing may be committed")
        expect(not (repo / "writable" / "new.txt").exists(), "the agent must not run")

    def malformed(repo, baseline):
        result = run_bridge(bridge, repo, b"not a task prompt\n")
        expect(result.returncode == 2, f"expected exit 2, got {result.returncode}")
        expect(commits(repo) == baseline, "nothing may be committed")

    return [
        ("in-boundary work commits", in_boundary),
        ("out-of-boundary write is refused", outside),
        ("forbidden path is refused", forbidden),
        ("rename into a forbidden path is refused", rename_into_forbidden),
        ("rename out of bounds is refused", rename_out_of_bounds),
        ("agent failure propagates", agent_failure),
        ("no changes is code 3", no_changes),
        ("missing commit message falls back", missing_message),
        ("failing pre-commit hook is code 5", failing_hook),
        ("dry run runs nothing", dry_run),
        ("malformed prompt is code 2", malformed),
    ]


def main():
    parser = argparse.ArgumentParser(description="End-to-end conformance for agent bridges.")
    parser.add_argument("--bridge", action="append", default=None, help="only this bridge (repeatable)")
    parser.add_argument("--list", action="store_true", help="list registered bridges")
    arguments = parser.parse_args()

    sys.path.insert(0, str(ROOT / "scripts" / "agents"))
    import bridge_kit

    selected = BRIDGES
    if arguments.bridge:
        selected = [bridge for bridge in BRIDGES if bridge["name"] in arguments.bridge]
        if not selected:
            known = ", ".join(bridge["name"] for bridge in BRIDGES)
            print(f"bridge-conformance: unknown bridge; registered: {known}", file=sys.stderr)
            return 2
    if arguments.list:
        for bridge in selected:
            print(f"{bridge['name']}\t{bridge['path']}")
        return 0

    if not STUB.exists():
        print(f"bridge-conformance: missing stub agent at {STUB}", file=sys.stderr)
        return 2

    failures = 0
    total = 0
    for bridge in selected:
        if not Path(bridge["path"]).exists():
            print(f"  FAIL {bridge['name']} :: bridge script missing at {bridge['path']}")
            failures += 1
            continue
        print(f"{bridge['name']}:")
        for name, body in commit_fixture(bridge, bridge_kit.encode_prompt):
            total += 1
            if not case(name, bridge, body, bridge_kit.encode_prompt):
                failures += 1

    if failures:
        print(f"bridge-conformance: {failures} of {total} case(s) failed")
        return 1
    print(f"bridge-conformance: {total} case(s) passed across {len(selected)} bridge(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
