# Plan: P6-M004 — Shared agent-bridge kit

Status: Complete
Milestone: P6-M004
Created: 2026-10-27

## Goal

Extract the agent-neutral half of `scripts/agents/claude-code-bridge` into a shared kit, port the
Claude Code bridge onto it with no behavior change, and add a conformance suite that proves the
boundary and commit contract for every bridge. Pin the `agentforge-task-prompt-v1` format with a
checked-in fixture that both the Rust encoder and the Python decoder must match.

## Non-goals

- No new bridge. Codex is P6-M005 and OpenCode is P6-M006.
- No change to instructions content, exit codes, commit trailers, or the tool allowlist for Claude.
  Any improvement to those belongs to the bridge that needs it, not to this refactor.
- No change to the adapter contract, the prompt format, or the profile format.
- No sandbox. The path check stays after the fact, as ADR-0043 states.
- No rewriting the bridge in Rust. Quantitative parity with today's behavior matters more than
  language purity in this milestone.

## Context

`claude-code-bridge` is 535 lines in one file. It contains four separable concerns:

1. a strict decoder and a mirrored encoder for `agentforge-task-prompt-v1`;
2. the path-boundary rules, changed-path discovery with rename and copy sources, and the isolated
   `CARGO_TARGET_DIR` for linked worktrees;
3. the commit, with a fallback message, trailers, and the summary tail;
4. Claude-specific policy: instruction text, `claude -p` argv, the tool allowlist and denylist, the
   `mcp.json` shape, and the `Co-Authored-By: Claude` trailer.

Concerns 1–3 are identical for any future agent and are exactly the parts that must not drift.
Concern 4 is what makes a bridge a bridge.

The current tests are `--self-test` (round-trip plus targeted rejections, path rules, script tools,
MCP arguments, target-dir selection) and one CI invocation of it. Two gaps matter:

- the decoder is only ever compared against the encoder in the same file, while the real producer is
  `render_task_prompt` in `crates/agentforge-adapter`;
- nothing exercises the failure paths end to end: an out-of-boundary write, a rename out of bounds,
  an agent that fails, or a failing pre-commit hook are all asserted as unit predicates, never as
  the actual behavior of the script.

## Architecture placement

`scripts/agents/` only, plus a test-only assertion in `crates/agentforge-adapter`. The adapter, the
orchestrator, and the profile format are untouched: a bridge is still an ordinary executable that
receives the prompt on stdin.

## Data flow

```
adapter ──render_task_prompt──▶ stdin ──▶ bridge
                                            │ decode (kit)
                                            │ instructions + argv (bridge)
                                            ▼
                                         agent process
                                            │
                                            ▼
                              changed paths ──▶ boundary check (kit)
                                            │
                                            ▼
                              commit + trailers (kit) ──▶ pre-commit gate
```

## Invariants

- Exit codes, trailers, and the prompt format do not change.
- A path outside the allowed set, or inside a forbidden one, is never committed, including the
  source side of a rename or copy.
- An agent failure never produces a commit.
- A commit is made only by the bridge, never by the agent.
- The kit installs no default agent policy; each bridge states its own.
- The Rust encoder and the Python decoder are mutually pinned by one fixture.

## ADRs

- ADR-0058 — one bridge kit, thin per-agent bridges, and a conformance gate.
- ADR-0043 — a real agent's work flows through a bridge that owns verification and commits.
- ADR-0056 — Phase A: bridge kit, then Codex, then OpenCode.

## Public API / CLI

Unchanged for operators: `scripts/agents/claude-code-bridge` keeps `--claude`, `--forge`,
`--model`, `--dry-run`, and `--self-test`, and keeps its exit codes.

New, developer-facing only:

- `python3 scripts/agents/bridge_kit.py --self-test` — the kit's own tests, including the fixture.
- `python3 scripts/agents/bridge_conformance.py [--bridge PATH ...]` — the end-to-end suite.

## Compatibility analysis

Existing profiles name the bridge script and `python3`; that path is unchanged. The bridge gains a
`sys.path` entry for its own directory so it can import the kit, which requires the checkout to
contain `scripts/agents/bridge_kit.py`; a bridge copied out of the tree without the kit now fails
loudly at import rather than silently working. Instruction text, tool policy, and commit trailers
are byte-identical, verified by diffing `--dry-run` output before and after.

## Dependency analysis

Python 3 standard library only, matching the existing bridge. No new crate. The Rust change is a
test inside the existing `agentforge-adapter` test module.

## Expected file boundary

- `.plans/P6-M004-shared-bridge-kit.plan.md`, `.plans/ACTIVE`
- `docs/adr/ADR-0058-shared-agent-bridge-kit.md`, `docs/adr/README.md`
- `scripts/agents/bridge_kit.py` (new)
- `scripts/agents/bridge_conformance.py` (new)
- `scripts/agents/fixtures/task-prompt-v1.txt` (new)
- `scripts/agents/claude-code-bridge` (refactor onto the kit)
- `crates/agentforge-adapter/src/lib.rs` (test-only fixture assertion)
- `.github/workflows/ci.yml` (run the kit self-test and the conformance suite)
- `docs/AGENT_PROFILES.md`, `docs/MILESTONES.md`
- `PROJECT_STATE.md`, `CHANGELOG.md`

## Test-first matrix

| Check | Test | Expected |
| --- | --- | --- |
| K1 fixture is produced by Rust | `cargo test -p agentforge-adapter` | `render_task_prompt` equals the checked-in fixture byte for byte |
| K2 fixture is read by the kit | kit `--self-test` | the fixture decodes to the expected fields and re-encodes identically |
| K3 malformed prompts refused | kit `--self-test` | truncated, wrong header, bad length, renamed field, and trailing bytes all raise `PromptError` |
| K4 boundary rules | kit `--self-test` | nested paths allowed, prefixes not, forbidden wins, `./` and trailing `/` normalized |
| K5 commit shape | kit `--self-test` | trailers, fallback message, and summary tail are produced exactly |
| K6 in-boundary work commits | conformance | exit 0, exactly one commit, agent message and both trailers present |
| K7 out-of-boundary write | conformance | exit 4, no commit, offending file left in the worktree |
| K8 forbidden path | conformance | exit 4, no commit |
| K9 rename out of bounds | conformance | exit 4, no commit, both sides reported |
| K10 agent failure | conformance | the agent's own exit status propagates, no commit |
| K11 no changes | conformance | exit 3, no commit |
| K12 missing commit message | conformance | exit 0, generated fallback subject and trailers |
| K13 failing pre-commit hook | conformance | exit 5, no commit, worktree preserved |
| K14 dry run | conformance | exit 0, command printed, nothing executed or committed |
| K15 malformed prompt | conformance | exit 2 before the agent runs |
| K16 Claude parity | local diff, recorded | `--dry-run` output for plain, scripts, and MCP task shapes matches the pre-refactor bridge after normalizing the scratch path |

K1 and K2 are the anti-drift pair. K6–K15 run once per registered bridge, so adding Codex in
P6-M005 means adding a registry entry, not new cases.

## Implementation sequence

1. Add the fixture and the Rust assertion. Generate the fixture once from the current renderer with
   a temporary test that writes the bytes, commit it, then delete the temporary test so the only
   remaining assertion is the comparison. Capture the pre-refactor `--dry-run` output for three task
   shapes for K16.
2. Create `scripts/agents/bridge_kit.py` with the prompt codec, path rules, changed paths, worktree
   target directory, commit builder, and exit-code constants, moving the existing functions without
   changing them. Its `--self-test` covers K2–K5 and decodes the fixture.
3. Port `claude-code-bridge` onto the kit, keeping its instruction text, argv, tool policy, and MCP
   config rendering in the bridge. Keep `--self-test`, narrowed to the Claude-specific parts plus
   the fixture check. Re-diff K16.
4. Write `scripts/agents/bridge_conformance.py`: a registry of bridges and their agent flag, a stub
   agent driven by environment variables, throwaway git repositories with explicit
   `user.name`/`user.email` and no hooks unless a case installs one, and the K6–K15 cases. Run it
   against the Claude bridge until green.
5. Wire both commands into the `Repository policy` CI job, update `docs/AGENT_PROFILES.md`, then
   close the milestone.

## Failure modes

- **Instructions text drifts during extraction.** K16 catches it. A wanted improvement is a separate
  change with its own rationale, not a side effect of moving code.
- **A throwaway repository has no git identity.** Commit fails and the suite reports a false
  negative. Every temporary repository gets `user.name` and `user.email` set explicitly.
- **The stub agent cannot find the commit-message path.** It scans its argv and stdin for a path
  ending in `COMMIT_MSG`, which works for a bridge that passes the prompt either way.
- **The stub is not executable, or Windows cannot run it.** The suite invokes the stub through the
  current interpreter and is confined to the Linux `Repository policy` job.
- **`cargo metadata` fails in a throwaway repository.** The existing helper already treats that as
  "no isolated target directory"; the suite must not depend on Cargo being present.
- **The kit grows a default that suits Claude.** Review rule from ADR-0058: agent-specific decisions
  stay in the bridge, and the kit exposes neutral facts only.

## Documentation impact

- `docs/AGENT_PROFILES.md`: replace "Real agents: the Claude Code bridge" with a bridge-kit section
  that keeps the Claude profile example and states the exit codes, the fixture, and how to run the
  conformance suite.
- `docs/adr/README.md`: register ADR-0058.
- `docs/MILESTONES.md`, `PROJECT_STATE.md`, `CHANGELOG.md`: closure evidence.
- ADR-0056's Phase A list needs no change; this is its first item.

## Quality gates

- `cargo test -p agentforge-adapter`
- `./scripts/agents/claude-code-bridge --self-test`
- `python3 scripts/agents/bridge_kit.py --self-test`
- `python3 scripts/agents/bridge_conformance.py`
- `./scripts/gate.sh full`
- `cargo run -p xtask --locked -- validate` and `validate-plan-policy`
- `./scripts/check-text-files`
- All seven CI jobs green on a pushed commit.

## Acceptance criteria

- [x] A checked-in `agentforge-task-prompt-v1` fixture is asserted by both the Rust encoder and the
      kit's decoder.
- [x] The shared kit owns the codec, boundary rules, changed-path discovery, and commit contract.
- [x] The Claude Code bridge keeps identical exit codes, trailers, and instruction text.
- [x] The conformance suite covers in-boundary work, out-of-boundary writes, forbidden paths,
      renames out of bounds, agent failure, no changes, a missing message, a failing pre-commit
      hook, a dry run, and malformed input.
- [x] The suite runs in CI and reports per-bridge results.
- [x] No new runtime dependency and no change to the adapter contract.

## Completion record

Implementation commit: `cfebb36`
CI run: `40633818472` — push event on `cfebb36`, seven of seven jobs green, including the new
`Repository policy` steps that run the kit self-test and the conformance suite
CI result: success
Completed: 2026-10-27
Notes: Behavior parity was checked, not assumed. The pre-refactor bridge's `--dry-run` output was
captured for the plain, `run_local_commands`, and `use_mcp_tools` task shapes, and the ported
bridge reproduced all three byte for byte after normalizing the random scratch path (2171, 2601,
and 2647 bytes). The suite is itself mutation-tested: disabling the boundary check makes four of
eleven cases fail, so a green run means something.

The fixture closed a real gap. The Python decoder previously round-tripped only against its own
encoder while the real producer is `render_task_prompt` in Rust; both now assert
`scripts/agents/fixtures/task-prompt-v1.txt` byte for byte, so either side drifting fails a test.
The workspace suite went from 349 to 350 tests. No new dependency, no MSRV change, and the adapter
contract, prompt format, and profile format are untouched.
