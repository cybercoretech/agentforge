# Plan: P6-M006 — Codex preflight and the corrected sandbox finding

Status: Approved
Milestone: P6-M006
Created: 2026-10-27

## Goal

Make the Codex bridge fail early, specifically, and with a remedy: add an automatic preflight that
verifies the executable, the flags the bridge passes, the sandbox runtime, a configured local
provider, and every write root before any model token is spent; map the remaining common Codex
failures to actionable hints without changing the exit-code contract; correct the two false limits
recorded in P6-M005; and check in a reproducible end-to-end harness that drives the real Codex binary
and skips honestly when it is absent.

## Non-goals

- No change to the kit's neutral contract, exit codes, trailers, or the commit rules.
- No new conformance case. The existing eleven cases still run per bridge (ADR-0058).
- No unsandboxed fallback, no `danger-full-access`, no `--worktree`, no bypass flags.
- No OpenCode work; that is P6-M007. No `forge doctor --agents`; that is P6-M008.
- No claim that the stub provider is a model. It proves plumbing, not quality.
- No rewrite of the closed P6-M004/P6-M005 plans; corrections live in ADR-0060 and state docs.

## Context

P6-M005 recorded two limits that later proved wrong (ADR-0060):

1. the `bwrap: execvp .../codex: No such file or directory` failure was caused by a degenerate
   synthetic permission profile with an empty `[permissions.ws]` table, not by the mise install path;
2. a live Codex turn was possible without credentials, because `codex exec --oss --local-provider
   ollama` speaks the OpenAI Responses API to a local endpoint.

A real turn was subsequently driven through the bridge in a throwaway repository: it issued an
`exec_command` tool call, the command ran inside the sandbox, and the bridge committed the model's
own message with `AgentForge-Task` and `AgentForge-Agent` trailers. A second run that wrote outside
the allowed paths exited 4 and committed nothing.

That proves the run path, but the operator experience is still poor in four measured ways:

| Observed failure | What the operator sees |
| --- | --- |
| Ollama is not running | `OSS setup failed: No running Ollama server detected` after Codex starts |
| Ollama is older than 0.13.4 | `OSS setup failed: Ollama X is too old` after Codex starts |
| a Codex upgrade removes or renames a flag | an argparse error from Codex, mid-run |
| a write root is missing (fresh `CARGO_TARGET_DIR`) | a sandbox or build failure with no named cause |

The last one matters in every linked worktree: `bridge_kit.agent_environment` assigns
`<target>/agentforge-worktrees/<name>`, and that directory does not exist until a build creates it.

Two more facts shape the design:

- Codex's built-in `ollama` provider cannot be overridden (`model_providers` rejects the reserved
  name) and does not honour `OLLAMA_HOST`; it uses `localhost:11434`. The end-to-end harness must
  therefore bind `11434` and skip if it is already taken.
- the GitHub `ubuntu-24.04` runner image does not install `bubblewrap`, so the sandbox-runtime probe
  must apply to a real Codex executable, not to the conformance stub.

## Architecture placement

`scripts/agents/codex-bridge` keeps its place as a thin Codex adapter and gains a preflight stage
plus a failure-hint mapper. `scripts/agents/conformance_stub.py` learns `--version` and
`exec --help` so the preflight is exercised by the existing suite instead of bypassed.
`scripts/agents/codex_e2e.py` is a new, opt-in harness beside the other agent scripts. No crate,
adapter, orchestrator, or profile-format change.

## Data flow

```text
stdin prompt ──decode (kit)──▶ task
                                 │
                                 ▼
                        build_command (argv)
                                 │
                    ┌────────────┴────────────┐
                    │ --dry-run               │ preflight (default)
                    ▼                         ▼
              print, exit 0         version · help · runtime · provider · roots
                                          │ fail ──▶ exit 2 + named remedy
                                          ▼
                                     codex exec
                                          │ non-zero
                                          ▼
                                 hint mapper ──▶ same exit status
```

## Invariants

- Preflight runs by default; `--no-preflight` is explicit; `--dry-run` never runs it.
- A preflight failure exits 2 and names one cause and one remedy.
- The exit-code contract is unchanged: 0, 2, 3, 4, 5, otherwise Codex's own status.
- The bridge never falls back to an unsandboxed run.
- `--worktree`, `danger-full-access`, and both `--dangerously-bypass-*` flags stay absent.
- The instructions stay on stdin and never enter argv.
- The target directory is created, not assumed.
- Hints are additive output; they never rewrite or hide the agent's status.

## ADRs

- ADR-0060 — bridge preflight and the corrected Codex finding (amends ADR-0059).
- ADR-0059 — Codex CLI as a first-party bridge.
- ADR-0058 — one kit, thin adapters, conformance as the price of first-party.
- ADR-0056 — Phase A order; policy, not sandbox, is the isolation boundary.

## Public API / CLI

`scripts/agents/codex-bridge`:

```text
--codex PATH                Codex executable (default: `codex` on PATH)
--forge PATH                forge executable for the MCP gateway
--model NAME                model to request
--local-provider PROVIDER   ollama or lmstudio; implies --oss
--writable-dir PATH         extra sandbox write root (repeatable, must be absolute)
--respect-user-config       load the operator's ~/.codex/config.toml
--preflight / --no-preflight   verify the environment first (default: on)
--dry-run                   print the instructions and command, run nothing, no preflight
--self-test                 check Codex policy, preflight rules, and the shared fixture
```

`scripts/agents/codex_e2e.py [--codex PATH]`: exit 0 on pass or `SKIP`, 1 on failure.

## Compatibility analysis

Additive. The default behaviour gains a preflight that passes on a working installation, so existing
profiles keep working. The conformance stub gains two informational responses; every existing case
keeps its expected code. Operators who deliberately run against a half-configured machine can pass
`--no-preflight`, but the bridge will still map the resulting failure to a hint.

## Dependency analysis

Python standard library only. The e2e harness uses `http.server` and `urllib`. No new crate, no new
package, no MSRV change.

## Expected file boundary

- `.plans/P6-M006-codex-preflight.plan.md`, `.plans/ACTIVE`
- `docs/adr/ADR-0060-bridge-preflight-and-corrected-codex-finding.md` (new)
- `docs/adr/ADR-0059-codex-cli-first-party-bridge.md` (pointer at the corrected consequence)
- `docs/adr/README.md`
- `scripts/agents/codex-bridge`
- `scripts/agents/conformance_stub.py`
- `scripts/agents/codex_e2e.py` (new)
- `scripts/agents/bridge_conformance.py` (milestone reference in the registry comment)
- `.github/workflows/ci.yml` (end-to-end step)
- `docs/AGENT_PROFILES.md`, `docs/MILESTONES.md`
- `PROJECT_STATE.md`, `CHANGELOG.md`

## Test-first matrix

| Check | Test | Expected |
| --- | --- | --- |
| C1 version probe | `codex-bridge --self-test` | a non-answering executable exits 2 with a named cause |
| C2 flag drift | `--self-test` | a missing long flag in help exits 2 and names the flag |
| C3 sandbox mode drift | `--self-test` | help without `workspace-write` exits 2 |
| C4 runtime probe | `--self-test` | a Codex identity without `bwrap` exits 2 on Linux; a stand-in skips it |
| C5 provider probe | `--self-test` | unreachable Ollama and an old version each exit 2 with a remedy |
| C6 provider version compare | `--self-test` | `0.13.3` fails, `0.13.4` and `0.20.0` pass |
| C7 write roots | `--self-test` | a missing target is created; an unwritable root exits 2 |
| C8 preflight switches | `--self-test` | default on, `--no-preflight` off, `--dry-run` skips |
| C9 hints | `--self-test` | each known stderr maps to one hint; unknown output maps to none |
| C10 preflight in conformance | `bridge_conformance.py` | the stub answers `--version`/`exec --help`; all eleven cases still pass |
| C11 dry run stays preflight-free | conformance | `--dry-run` succeeds with no executable help call |
| C12 end-to-end commit | `codex_e2e.py` | real Codex: exit 0, one commit, both trailers, file present |
| C13 end-to-end refusal | `codex_e2e.py` | real Codex: exit 4, no commit, offending file kept |
| C14 absent Codex | `codex_e2e.py` | prints `SKIP`, exit 0 |
| C15 corrected record | validators + review | ADR-0059 points at ADR-0060; state/docs no longer claim the false limits |

## Implementation sequence

1. Correct the record in the plan commit: ADR-0059 pointer, ADR-0060, ADR registry, MILESTONES row,
   PROJECT_STATE renumber and correction note, AGENT_PROFILES limits.
2. Add preflight and hints to `codex-bridge`, creating the target root first.
3. Teach `conformance_stub.py` `--version` and `exec --help`; keep all eleven cases unchanged.
4. Add `codex_e2e.py` with an in-process Responses-API stub on `11434`, the commit case, the
   path-violation case, and the `SKIP` path.
5. Add the CI end-to-end step and the CHANGELOG entry.
6. Run the full gate, push, confirm exact-head CI, close the milestone, tag it.

## Failure modes

- **Preflight is too strict and blocks a working run.** It derives required flags from the command it
  will actually pass, probes the provider only when one is configured, and probes `bwrap` only for a
  Codex identity. `--no-preflight` is the documented escape hatch.
- **`bwrap` is missing on a real machine.** Preflight exits 2 with the install remedy instead of
  letting Codex fail with `bwrap: execvp ...`.
- **Port 11434 is already in use.** The harness skips rather than hijacking an operator's Ollama.
- **The stub provider drifts from the Responses API.** The harness fails loudly; it does not fall
  back to a chat-completions shape Codex no longer accepts.
- **A hint misleads.** Hints are printed after the untouched agent output and never replace it, so
  the operator can always read the original error.
- **Codex changes its help layout.** The preflight fails and names the flag; that is the intended
  compatibility signal, not a bug.

## Documentation impact

- ADR-0060 is the record of the correction and the preflight obligation; ADR-0059 points at it.
- `docs/AGENT_PROFILES.md` gains the preflight section and loses the false limits.
- `PROJECT_STATE.md` corrects the P6-M005 evidence, renumbers OpenCode to P6-M007 and
  `forge doctor --agents` to P6-M008, and records P6-M006 evidence at closure.
- `docs/MILESTONES.md`, `CHANGELOG.md`: milestone row and operator-visible change.

## Quality gates

- `python3 scripts/agents/bridge_kit.py --self-test`
- `./scripts/agents/codex-bridge --self-test`
- `./scripts/agents/claude-code-bridge --self-test`
- `python3 scripts/agents/bridge_conformance.py`
- `python3 scripts/agents/codex_e2e.py` (real Codex on this machine; `SKIP` elsewhere)
- `./scripts/gate.sh full`
- `cargo run -p xtask --locked -- validate` and `validate-plan-policy`
- `./scripts/check-text-files`
- All seven CI jobs green on the exact pushed commit

## Acceptance criteria

- [ ] `--preflight` runs by default and exits 2, with a named cause and remedy, when the version
      probe, a required flag, `workspace-write`, the sandbox runtime, the local provider, or a write
      root fails.
- [ ] `--no-preflight` skips it; `--dry-run` skips it and never invokes the agent.
- [ ] The assigned target directory is created before Codex is started.
- [ ] Known Codex failures (missing binary, missing/old Ollama, removed wire API, bwrap failure,
      blocked write, missing credentials) each add one hint while preserving the exit status.
- [ ] `conformance_stub.py` answers `--version` and `exec --help`, and all twenty-two cases pass.
- [ ] `codex_e2e.py` drives the real binary to a commit, refuses an out-of-boundary write at exit 4,
      and prints `SKIP` with exit 0 when Codex is absent.
- [ ] ADR-0060 records the correction; ADR-0059, `PROJECT_STATE.md`, and `docs/AGENT_PROFILES.md` no
      longer state the two false limits.
- [ ] OpenCode is P6-M007 and `forge doctor --agents` is P6-M008 everywhere forward-looking.

## Completion record

Implementation commit:
CI run:
CI result:
Completed:
Notes:
