# Plan: P6-M005 — Codex CLI bridge

Status: Approved
Milestone: P6-M005
Created: 2026-10-27

## Goal

Add `scripts/agents/codex-bridge`, a first-party bridge for Codex CLI built on
`scripts/agents/bridge_kit.py`, that runs `codex exec` inside the AgentForge-managed worktree under
an explicit sandbox policy, grants only the write roots the task actually needs, reaches local
models through `--oss --local-provider`, and passes the existing eleven-case conformance suite
without new cases.

## Non-goals

- No change to the kit's neutral contract. Anything Codex-specific stays in the bridge.
- No new conformance cases. Adding a bridge means adding a registry entry (ADR-0058).
- No OpenCode work; that is P6-M006. No `forge doctor --agents`; that is P6-M007.
- No live model run is claimed. This machine has no Codex credentials and no local model, so the
  bridge is verified by construction, self-test, and conformance, and the gap is recorded.
- No attempt to make Codex own the worktree. `--worktree` stays forbidden.
- No change to the Claude Code bridge, the adapter contract, the prompt format, or profiles.

## Context

ADR-0058 left the kit and the Claude bridge in place with eleven conformance cases green and a
mutation test proving the suite can fail. Codex is the second bridge, and it is the first one that
brings its own sandbox, so it is where the "policy, not sandbox" position in ADR-0056 gets tested
against a tool that genuinely has a sandbox.

Verified against the installed `codex-cli 0.165.0` (`codex exec --help`):

| Flag | Meaning for this bridge |
| --- | --- |
| `-` as `PROMPT` | instructions are read from stdin |
| `-C, --cd <DIR>` | the working root; must be the AgentForge worktree |
| `-s, --sandbox <MODE>` | `read-only`, `workspace-write`, `danger-full-access` |
| `--worktree` | Codex-managed worktree — **forbidden** |
| `--dangerously-bypass-approvals-and-sandbox` | **forbidden** |
| `--dangerously-bypass-hook-trust` | **forbidden** |
| `--add-dir <DIR>` | extra writable roots alongside the workspace |
| `-c, --config <key=value>` | dotted TOML override, repeatable |
| `-m, --model <MODEL>` | model selection |
| `--oss`, `--local-provider <lmstudio\|ollama>` | local inference |
| `-o, --output-last-message <FILE>` | clean final message |
| `--ephemeral` | no session files on disk |
| `--ignore-user-config` | hermetic policy; auth still comes from `CODEX_HOME` |
| `--json`, `-i/--image`, `--output-schema` | unused here |

Sandbox behavior was measured rather than assumed, using `codex sandbox --permission-profile` with
an explicit `[permissions.<name>.filesystem]` map of path to `read` or `write`:

| Probe | Result |
| --- | --- |
| write inside the working root | succeeds, status 0 |
| write outside every granted root | `Read-only file system`, status 1 |
| write to an explicitly granted external root | succeeds |
| write to `~/.cargo` when not granted | `Read-only file system` |
| read outside the working root | permitted |
| outbound HTTPS | blocked in the sandbox; the host itself returns 200 |
| `cargo metadata --offline` with read-only `$CARGO_HOME` | succeeds |
| `cargo check` with a cold target dir and read-only `$CARGO_HOME` | succeeds |

So the practical constraint is narrow: a warm registry needs only reads, and the assigned
`CARGO_TARGET_DIR` must be granted because it lives outside the worktree by design
(`<target>/agentforge-worktrees/<name>`, matching `scripts/gate.sh`). Fetching an uncached
dependency needs both `use_network` and an explicit `--writable-dir "$CARGO_HOME"`.

Two limits are recorded, not softened:

- `codex login status` reports **not logged in**, and no local model is installed, so no live
  Codex turn can be demonstrated here;
- `codex sandbox` cannot re-exec a mise-installed Codex binary inside its own bwrap namespace on
  this machine (`bwrap: execvp .../codex: No such file or directory`, while the same binary runs
  under a plain `bwrap --ro-bind / /`). The measurements above used a copy of the binary in a bound
  path. This is an installation-layout limitation of the test harness, and it means the sandbox
  probes describe the policy schema, not this machine's ability to run Codex.

## Architecture placement

`scripts/agents/codex-bridge` only, plus a registry entry in `scripts/agents/bridge_conformance.py`
and one CI step. The kit, the adapter, the orchestrator, and the profile format are untouched.

## Data flow

```text
adapter ──render_task_prompt──▶ stdin ──▶ codex-bridge
                                            │ decode (kit)
                                            │ instructions (Codex text)
                                            │ argv: exec -C <worktree> -s workspace-write
                                            │       --add-dir <scratch>,<target>
                                            │       -c approval_policy="never"
                                            │       -o <scratch>/last-message.txt
                                            │       --ephemeral --ignore-user-config - 
                                            ▼
                                       codex exec  ── sandbox ──▶ commands
                                            │
                                            ▼
                              changed paths ──▶ boundary check (kit)
                                            │
                                            ▼
                              commit + trailers (kit) ──▶ pre-commit gate
```

## Invariants

- The argv never contains `--worktree`, `danger-full-access`, `--dangerously-bypass-approvals-and-sandbox`,
  or `--dangerously-bypass-hook-trust`.
- The instructions are never in argv.
- `-C` is the bridge's current directory, never a path the agent chose.
- Network is granted only when the task holds `use_network`.
- `$CARGO_HOME` is never granted implicitly.
- A missing last-message file is not an error.
- Exit codes, trailers, and the commit contract are the kit's, unchanged.

## ADRs

- ADR-0059 — Codex CLI as a first-party bridge.
- ADR-0058 — one kit, thin adapters, conformance as the price of first-party.
- ADR-0056 — Phase A order: kit, then Codex, then OpenCode.
- ADR-0043 — the bridge owns verification and the commit.

## Public API / CLI

New executable `scripts/agents/codex-bridge`:

```text
--codex PATH                 Codex executable (default: `codex` on PATH)
--forge PATH                 forge executable for the MCP gateway
--model NAME                 model to request
--local-provider PROVIDER    ollama or lmstudio; implies --oss
--writable-dir PATH          extra sandbox write root (repeatable)
--respect-user-config        load the operator's ~/.codex/config.toml
--dry-run                    print the instructions and command, run nothing
--self-test                  check Codex-specific policy and the shared fixture
```

Exit codes are the kit's: 0 committed, 2 invalid input or configuration, 3 no changes, 4 path
violation, 5 commit or pre-commit failure, otherwise Codex's own status.

## Compatibility analysis

Purely additive. No existing profile, script, or crate changes behavior. The conformance registry
gains one entry, so the existing eleven cases now run twice. The CI `Repository policy` job gains
one command.

## Dependency analysis

Python 3 standard library only, plus `tomllib` (3.11+, already required by `scripts/sbom-merge`).
No new crate, no MSRV change.

## Expected file boundary

- `.plans/P6-M005-codex-bridge.plan.md`, `.plans/ACTIVE`
- `docs/adr/ADR-0059-codex-cli-first-party-bridge.md`, `docs/adr/README.md`
- `scripts/agents/codex-bridge` (new)
- `scripts/agents/bridge_conformance.py` (registry entry)
- `.github/workflows/ci.yml` (self-test step)
- `docs/AGENT_PROFILES.md`, `docs/MILESTONES.md`
- `PROJECT_STATE.md`, `CHANGELOG.md`

## Test-first matrix

| Check | Test | Expected |
| --- | --- | --- |
| C1 `--worktree` is never passed | `codex-bridge --self-test` | argv construction has no `--worktree` |
| C2 bypass flags are never passed | `--self-test` | no `danger-full-access`, no `--dangerously-bypass-*` |
| C3 instructions stay off argv | `--self-test` | argv is short; the instruction text appears only on stdin |
| C4 `-C` is the working directory | `--self-test` | `-C` equals the absolute cwd |
| C5 sandbox mode is workspace-write | `--self-test` | `-s workspace-write` present exactly once |
| C6 approval policy is never | `--self-test` | `-c approval_policy="never"` present |
| C7 scratch and target dirs are granted | `--self-test` | both appear as `--add-dir` values when a target dir exists |
| C8 extra roots come only from flags | `--self-test` | `--writable-dir` adds roots; `$CARGO_HOME` never appears unasked |
| C9 network follows the capability | `--self-test` | `network_access=true` only with `use_network` |
| C10 MCP overrides are valid TOML | `--self-test` | `tomllib` round-trips the rendered `mcp_servers.agentforge` command and args |
| C11 MCP args equal the neutral argv | `--self-test` | bridge config equals `bridge_kit.mcp_server_arguments` |
| C12 missing last message is tolerated | `--self-test` | summary falls back to stdout without failing |
| C13 local provider implies `--oss` | `--self-test` | `--local-provider ollama` yields `--oss`; reject unknown providers |
| C14 shared fixture decodes | `--self-test` | `P6-M004-T0001` fields match the checked-in fixture |
| C15 conformance | `bridge_conformance.py` | all eleven cases pass for `codex` |
| C16 dry run | conformance | exit 0, command printed, nothing executed or committed |
| C17 malformed prompt | conformance | exit 2 before Codex runs |

C1–C14 are absent-behavior tests: they assert that something dangerous is *not* in the argv, which
is the failure mode review does not catch reliably.

## Implementation sequence

1. Write the bridge: kit import, Codex instruction text, argv builder, MCP overrides via `tomllib`,
   last-message handling, `--self-test`, `--dry-run`.
2. Add the registry entry to `bridge_conformance.py` and run the suite until both bridges pass.
3. Add the CI self-test step and the `AGENT_PROFILES.md` section.
4. Record the measured sandbox table and the two limits in `PROJECT_STATE.md` and the plan's
   completion record.

## Failure modes

- **A blocked write silently disappears.** Measured: it does not. The command fails with
  `Read-only file system` and a non-zero status, so the agent sees the failure.
- **The agent cannot write its commit message.** The scratch directory is a `--add-dir`; C7 pins it.
  Without it the run would still commit, but with the generated fallback message.
- **Cargo cannot write its target dir.** The assigned `CARGO_TARGET_DIR` is a `--add-dir`; without
  it every build fails. Only present in a linked worktree, which is where tasks actually run.
- **A cold registry blocks the build.** Recorded, and the remedy is two explicit grants. The bridge
  says so in the instructions it gives the agent instead of letting it thrash.
- **The operator's config widens the sandbox.** `--ignore-user-config` by default. C1/C2 pin the
  argv, so a config change cannot alter what the bridge passes.
- **`--add-dir` is not repeatable across many roots.** Only two roots are routine; extra roots are
  operator-supplied and few. If Codex rejects repeats, the run fails at exit != 0 and the bridge
  reports it rather than retrying.
- **Codex exits non-zero for an unrelated reason** (auth, quota). The bridge propagates the status
  unchanged, as the contract requires, and commits nothing.

## Documentation impact

- `docs/AGENT_PROFILES.md`: add "The Codex CLI bridge" beside the Claude section, with the profile
  example, the sandbox grants, the local-provider flags, and the recorded limits.
- `docs/adr/README.md`: register ADR-0059.
- `docs/MILESTONES.md`, `PROJECT_STATE.md`, `CHANGELOG.md`: closure evidence.

## Quality gates

- `python3 scripts/agents/bridge_kit.py --self-test`
- `./scripts/agents/codex-bridge --self-test`
- `./scripts/agents/claude-code-bridge --self-test`
- `python3 scripts/agents/bridge_conformance.py`
- `./scripts/gate.sh full`
- `cargo run -p xtask --locked -- validate` and `validate-plan-policy`
- `./scripts/check-text-files`
- All seven CI jobs green on a pushed commit.

## Acceptance criteria

- [ ] `scripts/agents/codex-bridge` runs `codex exec` on the kit with instructions on stdin, `-C`
      bound to the worktree, and `-s workspace-write`.
- [ ] `--worktree`, `danger-full-access`, and both `--dangerously-bypass-*` flags are asserted absent.
- [ ] The scratch directory and the assigned `CARGO_TARGET_DIR` are granted; `$CARGO_HOME` is not
      granted implicitly, and `--writable-dir` is the only way to add roots.
- [ ] Network is granted only when the task holds `use_network`.
- [ ] MCP reaches Codex through `-c mcp_servers.agentforge.*`, validated with `tomllib`.
- [ ] Local models are reachable through `--local-provider {ollama,lmstudio}`.
- [ ] All eleven conformance cases pass for `codex` with no new cases.
- [ ] The measured sandbox behavior and the two unproven limits (no live turn, mise/bwrap) are
      recorded in the docs, not softened.

## Completion record

Implementation commit:
CI run:
CI result:
Completed:
Notes:
