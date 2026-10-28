# Local agent profiles

AgentForge can keep a reviewed, project-local process profile under
`.forge/agents/`. Profiles are ordinary versioned text files. They describe only how
to start a local executable; they do not grant task capabilities, approvals, or repository access.

Each profile is named `<id>.conf` and uses one `key=value` field per line:

```text
version=1
executable=/absolute/path/to/agent
argument=--mode
argument=worker
env.FORGE_MODE=local
timeout_ms=60000
max_output_bytes=1048576
```

The supported environment form is `env.<NAME>=<VALUE>`; the child receives a cleared
environment plus only these explicit values. Arguments and values are literal strings. No shell is
invoked and no interpolation is performed.

Profiles are bounded to 64 KiB, 64 arguments, 64 environment values, a 24-hour timeout, and a
64 MiB output limit. The executable must be an existing absolute regular file. Duplicate fields,
unsafe profile IDs, malformed values, symlinked profile files, and out-of-range limits fail closed.

Inspect profiles without launching anything:

```text
forge agent list /path/to/project
forge agent validate /path/to/project local-agent
forge agent inspect /path/to/project local-agent
```

Select one for direct or daemon-backed execution:

```text
forge run /path/to/project P2-M007-T0001 --profile local-agent
forge daemon run /path/to/project P2-M007-T0001 --profile local-agent
```

For a direct foreground session where the operator must answer line-oriented agent questions, add
`--interactive` to `forge run`. The profile's literal executable, arguments, explicit environment,
timeouts, and evidence bounds remain in force; `forge daemon run` does not attach a terminal.

For a full-screen or raw-mode agent, add `--interactive --pty` instead. This allocates a native
pseudo-terminal, forwards keyboard input and terminal resize events, and restores the operator's
terminal mode when the child exits. PTY mode is intentionally direct-foreground only and requires
both stdin and stdout to be terminals; it fails closed in CI pipes and daemon execution.

The normal task preflight still applies: the task must exist, its managed worktree must be clean
and verified, and required approvals and capabilities must already be present.

## Real agents: bridges and the shared kit

Real coding agents read natural-language instructions, not the length-delimited
`agentforge-task-prompt-v1` document, and a task's changes only reach `forge task diff` and
`forge task integrate` once they are committed on the task branch.

A **bridge** closes that gap. Every bridge does the same four things (ADR-0043):

1. It strictly decodes the task prompt from stdin; malformed input exits 2 before any agent runs.
2. It runs the agent headless in the task worktree, under a bounded tool policy the bridge states.
3. It lists every changed path and refuses (exit 4, nothing committed, worktree kept) if any path
   is outside the task's allowed paths or inside a forbidden one.
4. It commits the changes itself, using the detailed message the agent writes to a scratch file,
   plus `AgentForge-Task`/`AgentForge-Agent` trailers. The repository's pre-commit hook runs on
   that commit, and a hook or gate failure exits 5.

Steps 1, 3, and 4 are identical for every agent, so they live in
`scripts/agents/bridge_kit.py` (ADR-0058). A bridge file contains only what is agent-specific: the
instruction text, the command line, the permission or sandbox policy, the MCP configuration shape,
and the co-author trailer. `scripts/agents/claude-code-bridge` is the reference bridge.

Exit codes, which every bridge preserves: 0 committed, 2 invalid input or configuration, 3 no
changes, 4 path violation, 5 commit/pre-commit failure; anything else is the agent's own exit
status. As with any adapter, exit 0 is evidence, not acceptance: project gates, review, and
`forge task accept` still follow.

The prompt format is pinned by `scripts/agents/fixtures/task-prompt-v1.txt`. A Rust test asserts
that `render_task_prompt` produces exactly those bytes, and the kit's self-test asserts that its
decoder reads exactly those bytes and re-encodes them identically, so the two implementations
cannot drift apart silently.

### The Claude Code bridge

Profile (the environment is cleared, so pass what Claude Code and your toolchain need):

```text
# .forge/agents/claude-code.conf
version=1
executable=/usr/bin/python3
argument=/path/to/agentforge/scripts/agents/claude-code-bridge
argument=--claude
argument=/absolute/path/to/claude
env.PATH=/usr/bin:/bin:/home/operator/.local/bin
env.HOME=/home/operator
timeout_ms=3600000
max_output_bytes=8388608
```

The bridge runs `claude -p` with `--permission-mode acceptEdits` and a bounded tool allowlist: read
and edit tools, `cargo`, `./scripts/gate.sh`, read-only `git`, and `ls`. `git commit`/`push`/
`reset`/`checkout`/`clean` and web tools are denied.

For tasks that hold `run_local_commands`, the bridge also lets Claude Code run files inside the
task's own allowed paths: `./<path>`, `python3 <path>`, and `bash <path>`, with any arguments
(P2-M035). An agent can then run the self-tests of scripts it writes; before, only `cargo`,
`./scripts/gate.sh`, and a few `git` commands were allowed (dogfooding finding 22). This grants
nothing new, since with `run_local_commands` the agent already runs arbitrary code through `cargo`.
Files outside the allowed paths stay off limits, and paths containing spaces are skipped.

For tasks that hold `use_mcp_tools`, the bridge also gives Claude Code the AgentForge MCP
task-tool gateway (P3-M005). Add `argument=--forge` and `argument=/absolute/path/to/forge` to the
profile, because the cleared environment has no useful `PATH`; see
[MCP_GATEWAY.md](MCP_GATEWAY.md#using-it-with-the-claude-code-bridge).

Add `argument=--model` and `argument=<model>` to pin a model. Use `--dry-run` (reads a prompt on
stdin and prints the instructions) to preview what the agent will be told.

### The Codex CLI bridge

```text
# .forge/agents/codex.conf
version=1
executable=/usr/bin/python3
argument=/path/to/agentforge/scripts/agents/codex-bridge
argument=--codex
argument=/absolute/path/to/codex
env.PATH=/usr/bin:/bin:/home/operator/.local/bin
env.HOME=/home/operator
timeout_ms=3600000
max_output_bytes=8388608
```

The bridge runs `codex exec` with the instructions on stdin (`-`), `-C` bound to the task worktree,
and `-s workspace-write` (ADR-0059). `--worktree` is never passed: AgentForge owns task worktrees,
so a Codex-created one would sit outside the verification and integration path. The two
`--dangerously-bypass-*` flags are never passed either.

Codex brings its own sandbox, which reads everything and writes only to granted roots. The bridge
grants the scratch directory (so the agent can write its commit message) and the task's assigned
`CARGO_TARGET_DIR`. Writes outside those roots fail with `Read-only file system` and a non-zero
status, so a blocked command is visible to the agent instead of silently dropped.

`$CARGO_HOME` is **not** granted implicitly. A warm registry needs only reads — `cargo metadata` and
a cold-target-directory `cargo check` both succeed against a read-only `$CARGO_HOME` — but fetching
an uncached dependency needs both the `use_network` capability and an explicit root:

```text
argument=--writable-dir
argument=/home/operator/.cargo
```

Network access is granted only for tasks that hold `use_network`, through
`-c sandbox_workspace_write.network_access=true`. Approval policy is `never`, because nobody is
present in a headless run to approve an escalation; a blocked command fails rather than waiting.

For tasks that hold `use_mcp_tools`, add `argument=--forge` and `argument=/absolute/path/to/forge`.
The gateway reaches Codex as `-c mcp_servers.agentforge.command` and `.args` overrides, rendered as
TOML and parsed back with `tomllib` before the run, so a malformed override exits 2.

Local models use `--oss`, which the bridge adds for you:

```text
argument=--local-provider
argument=ollama
```

`lmstudio` is the other supported value. Sessions are `--ephemeral`, and the operator's
`~/.codex/config.toml` is ignored by default so it cannot silently widen the policy; pass
`--respect-user-config` to load it (needed for custom providers). Credentials still come from
`CODEX_HOME`.

**Verified limits and the correction.** The sandbox behavior above was measured with
`codex-cli 0.165.0`. P6-M005 originally recorded that no live Codex turn could be run here and that
the sandbox could not re-exec a mise-installed binary; ADR-0060 corrects both. A real turn was
subsequently driven through the bridge without credentials, against a stub local provider that
speaks the Responses API: the model issued a sandboxed command, the bridge committed the result, and
a run that wrote outside the allowed paths exited 4 and committed nothing. The earlier
`bwrap: execvp ...: No such file or directory` came from a degenerate synthetic permission profile,
not from the install path.

### Working on a bridge

```text
python3 scripts/agents/bridge_kit.py --self-test       # the shared kit, including the fixture
./scripts/agents/claude-code-bridge --self-test        # the bridge's own policy
./scripts/agents/codex-bridge --self-test              # Codex's sandbox and argv policy
python3 scripts/agents/bridge_conformance.py           # every registered bridge, end to end
python3 scripts/agents/bridge_conformance.py --list    # which bridges are registered
```

The conformance suite drives each bridge against a stub agent in a throwaway Git repository and
covers in-boundary work, an out-of-boundary write, a forbidden path, renames in both directions,
agent failure, no changes, a missing commit message, a failing pre-commit hook, a dry run, and
malformed input. A bridge that does not pass is not first-party: adding one means adding a registry
entry in `scripts/agents/bridge_conformance.py`, not new cases. All of these run in CI.

Every run's full stdout and stderr, whether bridge or agent, are saved under
`.forge/evidence/<task-id>/` and referenced by the `AgentFinished` audit event. `forge task launch`
prints the agent's exit code and the log paths. When the agent fails, it prints the last lines of
output and exits 1. In a linked worktree the bridge also gives the agent an isolated
`CARGO_TARGET_DIR` (`<target>/agentforge-worktrees/<worktree name>`, the same one
`scripts/gate.sh` uses), so builds the agent runs directly cannot collide with other checkouts.
The path check runs after the agent, so it is a commit guard, not a sandbox. See
[`DOGFOODING.md`](DOGFOODING.md) for a full run on AgentForge itself.
