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
`CARGO_TARGET_DIR`, and it creates the target directory before the run: a fresh linked worktree does
not have one yet. Writes outside those roots fail with `Read-only file system` and a non-zero
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

**Preflight.** Before the agent starts, the bridge verifies the executable (`--version`), every long
flag the command it built actually uses (against `codex exec --help`, plus `workspace-write`), the
Linux sandbox runtime for a real Codex (`bwrap` and a working user namespace), a configured local
provider (Ollama 0.13.4 or newer on `11434`, LM Studio on `1234`), and every write root. A failure
exits 2 with one named cause and one remedy, before any model token is spent. `--no-preflight` skips
the checks; `--dry-run` skips them too, so instructions can be read on a machine without Codex.

The bridge never falls back to an unsandboxed run. When Codex itself fails, the bridge adds one
actionable hint (missing or old Ollama, a removed wire API, a bubblewrap failure, a blocked write,
missing credentials, an endpoint without the Responses API) and otherwise leaves the exit status and
output untouched.

The reusable proof is `scripts/agents/codex_e2e.py`: it starts a stub Responses-API endpoint on
`11434`, drives the real binary through the bridge to a commit and to an exit-4 refusal, and prints
`SKIP` with exit 0 when Codex is not installed. If a local Ollama already owns `11434`, it skips
rather than interfering.

### The OpenCode bridge

```text
# .forge/agents/opencode.conf
version=1
executable=/usr/bin/python3
argument=/path/to/agentforge/scripts/agents/opencode-bridge
argument=--opencode
argument=/absolute/path/to/opencode
env.PATH=/usr/bin:/bin:/home/operator/.local/bin
env.HOME=/home/operator
timeout_ms=3600000
max_output_bytes=8388608
```

The bridge runs `opencode run --standalone --format json --agent agentforge-<hex>` with the
instructions on stdin. `--standalone` is mandatory: without it `run` talks to the background
service, whose configuration, plugins, and permissions are not the bridge's. The bridge parses the
NDJSON stream: `text` parts become the summary, `tool_use` events are reported, an `error` event is
a failure even when the process exits 0, and a line that is not JSON is forwarded as written.

**Policy, not a sandbox.** OpenCode 2.2.0 has no operating-system sandbox. Isolation is an ordered
list of permission rules (`action`, `resource`, `effect`; last match wins), and the bridge builds
that list itself in `OPENCODE_CONFIG_CONTENT`, a virtual config source OpenCode appends last
(ADR-0061). The worktree cannot add, remove, or reorder a rule: the bridge also sets
`OPENCODE_CONFIG_PROJECT_DISABLE=true`, which turns off project `opencode.json(c)` and project
`.opencode/plugins` discovery, and its preflight proves both for the installed build.

The generated agent starts with a catch-all deny; allows `read`, `glob`, `grep`, and `edit` inside
the worktree; denies `.env` and `.env.*` for both reading and editing; denies `question`, `skill`,
`subagent`, the browser, and Code Mode; and denies every `external_directory` except the scratch
directory and the assigned `CARGO_TARGET_DIR`. No rule is `ask`, because a headless run has nobody
to answer. Code Mode is denied because its `execute` tool documents that `fetch` is available to the
scripted runtime, which would grant network access outside `webfetch`/`websearch` and outside the
`use_network` capability.

`shell` follows `run_local_commands`: a task without the capability gets no shell at all, and a task
with it may run commands, with a few destructive Git commands denied as defence in depth. This is
the honest difference from Codex — `shell` runs as the operator and no rule list confines it, so the
capability stays a human decision. `webfetch` and `websearch` follow `use_network`.

**The worktree guard.** `opencode_worktree_remove` is not gated by permission rules. During probing
it deleted the active linked worktree with `force: true`; a catch-all deny, `worktree*` actions, and
`tool.use` policies all left it working. The bridge therefore loads
`scripts/agents/opencode-plugins/agentforge-guard/`, a local plugin directory that removes
`opencode_worktree_create`, `_list`, and `_remove` through OpenCode's tool transform and records its
before/after inventory to a marker file. With the guard loaded, the same forced call fails with
`No tool named "opencode_worktree_remove" is currently available`, and the worktree survives. The
preflight requires an `after` record that omits the three tools, so a future build that stops
honouring the transform fails before the agent starts.

**MCP is refused in this milestone.** A configured MCP server connects, but its tools are absent
from the first headless request and appear only after a step, and `opencode mcp list` cannot even
report the server. Rather than run without `check_changes` and `run_gate`, the bridge exits 2 for a
task holding `use_mcp_tools`, names the measured cause, and points at the Claude Code or Codex
bridge. Enabling it later is a separate milestone with its own evidence.

**Preflight.** Before the agent starts, the bridge verifies the executable (`--version`), every long
flag the built command uses (against `run --help`), the generated agent in
`debug agents --standalone` with `mode: primary`, the guard marker, project isolation (a planted
project plugin must not load and no config source may live inside the worktree), the model in
`models --standalone` when `--model` is given, and that the scratch and target roots are writable. A
failure exits 2 with one cause and one remedy. `--no-preflight` skips the checks; `--dry-run` skips
them too.

The reusable proof is `scripts/agents/opencode_e2e.py`: it serves a streaming OpenAI-compatible stub
provider on an ephemeral port, writes a throwaway XDG config that registers it, and drives the real
binary to a commit, to an exit-4 path refusal, through a forced worktree removal that fails, and to a
rejected provider request with one credential hint. It prints `SKIP` with exit 0 when OpenCode is
absent. The stub is not a model, and this harness does not claim a live-model turn.

**Verified limits.** The data directory is not isolated, because it holds OpenCode's auth database;
sessions therefore accumulate in the operator's OpenCode store, and the run uses the operator's
credentials. `snapshots` is off so OpenCode writes no snapshot store into the worktree, and the
title agent is disabled so a run spends no auxiliary request naming its session.

### Working on a bridge

```text
python3 scripts/agents/bridge_kit.py --self-test       # the shared kit, including the fixture
./scripts/agents/claude-code-bridge --self-test        # the bridge's own policy
./scripts/agents/codex-bridge --self-test              # Codex's sandbox and argv policy
./scripts/agents/opencode-bridge --self-test           # OpenCode's policy, config, and decoder
python3 scripts/agents/bridge_conformance.py           # every registered bridge, end to end
python3 scripts/agents/bridge_conformance.py --list    # which bridges are registered
python3 scripts/agents/codex_e2e.py                    # real Codex, or SKIP when it is absent
python3 scripts/agents/opencode_e2e.py                 # real OpenCode, or SKIP when it is absent
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
