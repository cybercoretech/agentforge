# Plan: P6-M007 — The OpenCode bridge, its policy, and the worktree guard

Status: Approved
Milestone: P6-M007
Created: 2026-10-28

## Goal

Add OpenCode as the second first-party real-agent bridge. It runs headless on the shared kit
(`bridge_kit.py`), is configured entirely by a bridge-owned document that the worktree cannot
loosen, refuses a task whose surface it cannot honour instead of silently dropping it, and cannot
delete the AgentForge worktree it is running in.

OpenCode 2.2.0 has **no OS sandbox**. Its isolation is a list of ordered permission rules
(`action`, `resource`, `effect`, last match wins). ADR-0056 already says the bridge boundary is
policy, not a sandbox; this milestone makes that concrete, names where the policy is weaker than
Codex's sandbox, and closes the one measured hole that made a run destructive.

## Non-goals

- No OS-level sandbox for OpenCode. None exists in 2.2.0; claiming one would be false.
- No MCP support in this milestone. `use_mcp_tools` tasks are refused with a cause and a remedy
  (see the finding below), not run with the tool silently missing.
- No change to the kit's codec, exit codes, trailers, or commit rules. No new conformance case.
- No `forge doctor --agents` (P6-M008), no profile-format change, no crate change.
- No rewrite of the closed P6-M004/P6-M005/P6-M006 plans; this milestone's corrections live in
  ADR-0061 and the state docs.
- No claim that the stub provider is a model, or that a policy-configured run is sandboxed.

## Context

The plan order is shared kit → Codex → OpenCode (ADR-0056). The kit and Codex are complete; the
conformance registry has a placeholder for this milestone. Probing `opencode v2.2.0` on this machine
settled the design and found one showstopper.

### Measured behaviour that the bridge depends on

| Question | Answer (v2.2.0) |
| --- | --- |
| How is the prompt passed? | `opencode run` reads it from **stdin** when no positional message is given; `/dev/null` exits 1 with `You must provide a message`. |
| Machine-readable output | `--format json` writes NDJSON: `step_start`, `text` (`part.text`), `tool_use` (`part.tool`, `part.state.status/error/output`), `step_finish`, `error` (`error.message/status/response.body`). The final answer is the concatenation of `text` parts. |
| Exit status | 1 on provider errors, 0 on success. An `error` event can still appear, so the stream is parsed rather than trusted blindly. |
| Bridge-owned config | `OPENCODE_CONFIG_CONTENT` is a virtual source appended **last** to the config chain; verified with `opencode debug config --standalone`. |
| Worktree must not loosen it | `OPENCODE_CONFIG_PROJECT_DISABLE=true` removes project `opencode.json(c)` **and** project `.opencode/plugins` discovery; verified by planting a project plugin that loads with the switch off and does not load with it on. |
| Operator credentials | Auth lives in `~/.local/share/opencode/opencode.db`, so the data directory is **not** isolated. `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_STATE_HOME`/`XDG_CACHE_HOME` are honoured where a harness wants isolation. |
| Agent selection | `--agent <id>` works for an agent defined in the config. A unique per-run id avoids colliding with any global agent of the same name. |
| Permissions | Ordered rules; no match means `ask`. A catch-all `{action:"*",effect:"deny"}` followed by explicit allows works, and a denied `shell` removes the tool. Agent rules are appended after the built-in base, so the bridge's rules are last. |
| Outside the worktree | `external_directory: deny *` blocks `read`, `grep`, and `glob` outside the worktree, and blocks `edit` outside it. It is not a filesystem sandbox for `shell`. |
| Secrets | `read *.env` and `read *.env.*` deny both `.env` and `sub/.env` (the glob crosses directories). |
| Non-file tools | A denied `skill` still appears in the offered tool list but fails with `Permission denied: skill`. `question` disappears. Code Mode (`execute`) offers `fetch`. |

### Finding: `opencode_worktree_remove` ignored the policy and deleted a live worktree

`opencode_worktree_remove` is offered to the model in the default tool set. With
`{"directory": "<active linked worktree>", "force": true}` it **deleted that worktree**
(`git worktree list` afterwards showed only the main checkout). It is not gated by permission rules:
`worktree`, `worktree_remove`, `worktree_*`, `opencode_worktree_remove`, `tool.use` policies, and a
catch-all deny were all tried and all left the tool working.

**Mitigation, verified:** a bridge-owned OpenCode plugin loaded from the config content removes
`opencode_worktree_create`, `opencode_worktree_list`, and `opencode_worktree_remove` with
`ctx.tool.transform((editor) => editor.remove(id))`. With the guard loaded, the same forced call
failed with `No tool named "opencode_worktree_remove" is currently available`, and the linked
worktree survived. The plugin loads from a plain directory (`package.json` + `index.ts`) with no
npm install and no network, and it records its before/after tool inventory to a marker file so the
preflight can prove it loaded in this build.

The worktree row is the reason this milestone is not "just another adapter": without the guard the
bridge could destroy the checkout that verification and integration depend on.

### Finding: MCP tools are not usable in a headless run

With `mcp.servers.<name>` configured, the server connects (`mcp connected … tools=1` in the log) but
the direct MCP tool is absent from the first model request. It appears only after a step has already
happened, and only in some combinations. Code Mode is the other route, and Code Mode is unacceptable:

- the `execute` description states that `fetch` is available to the scripted runtime, so allowing it
  grants network access outside `webfetch`/`websearch` and outside `use_network`;
- it hides tools behind a second, scripted surface that the ordered rules do not describe.

`opencode mcp list` also prints `No MCP servers configured` for a configured server in this build, so
it cannot be used as a preflight probe. Therefore the bridge **refuses** a task that holds
`use_mcp_tools`, with the cause and the remedy (run it with Claude Code or Codex, or wait for an
OpenCode build with a stable MCP surface). Silently running without `check_changes`/`run_gate` would
be a capability loss disguised as success.

## Architecture placement

```text
scripts/agents/opencode-bridge            the adapter (policy, argv, config, NDJSON, preflight)
scripts/agents/opencode-plugins/
  agentforge-guard/package.json           a local OpenCode plugin: a directory, not a file
  agentforge-guard/index.ts               removes the worktree tools; records a preflight marker
scripts/agents/opencode_e2e.py            real binary, stub provider, SKIP when absent
```

The bridge is a thin adapter over `bridge_kit.py` (ADR-0058): the kit decodes the prompt, checks the
path boundary, and commits. The bridge owns the OpenCode policy, the config document, the argv, the
NDJSON decoder, the preflight, the hints, and the co-author trailer.

## Data flow

```text
stdin prompt ──decode (kit)──▶ task
                                 │
                                 ▼
              policy + config content (bridge-owned env)
                                 │
                    ┌────────────┴────────────┐
                    │ --dry-run               │ preflight (default)
                    ▼                         ▼
              print, exit 0    version · run --help · agent+guard · project isolation
                                          │ fail ──▶ exit 2 + one cause and remedy
                                          ▼
                        opencode run --standalone --format json --agent <id>  (stdin = instructions)
                                          │ NDJSON
                                          ▼
                     text parts ──▶ summary      error event ──▶ hint, exit 1
                                          │ tool event (logged), exit status preserved
                                          ▼
                          changed paths (kit) · boundary (kit) · commit (kit)
```

## Invariants

- `OPENCODE_CONFIG_CONTENT` is the only policy source; it is built by the bridge and passed in the
  environment. The worktree cannot add, remove, or reorder a rule.
- `OPENCODE_CONFIG_PROJECT_DISABLE=true` is always set. Project config and project plugins do not
  load.
- `--standalone` is always passed. Without it `run` talks to the background service, which has its
  own config, plugins, and permissions.
- No `ask` can occur: every rule the bridge writes is `allow` or `deny`, and `question` is denied.
- The argv never contains `--auto`, `--continue`, `--session`, `--fork`, `--file`, `--title`,
  `--thinking`, or `--server`. The instructions go on stdin and never enter the argv.
- The guard plugin removes the three worktree tools; the preflight proves it loaded and that a probe
  project plugin did not.
- The exit-code contract is unchanged: 0, 2, 3, 4, 5, otherwise OpenCode's own status.
- A task with `use_mcp_tools` exits 2 with a cause and a remedy; it never runs without its tools.
- The bridge never claims a sandbox. The instructions say the run is policy-configured, and name
  `shell` as the part the policy cannot confine.

## The policy

Ordered rules in the generated agent, last match wins:

| Order | Action | Resource | Effect | Why |
| --- | --- | --- | --- | --- |
| 1 | `*` | `*` | deny | Nothing is allowed by omission. |
| 2 | `read`,`glob`,`grep`,`edit` | `*` | allow | The worktree, including new files. |
| 3 | `read` | `*.env`, `*.env.*` | deny | Secrets never reach the model. |
| 4 | `edit` | `*.env`, `*.env.*` | deny | Secrets never enter a commit. |
| 5 | `question`,`skill`,`subagent`,`execute`,`browser` | `*` | deny | No prompts, no injected instructions, no scripted runtime with `fetch`. |
| 6 | `external_directory` | `*` | deny | Everything outside the worktree. |
| 7 | `external_directory`,`read`,`edit` | `<scratch>/*` | allow | The commit message lives here. |
| 8 | `external_directory`,`read`,`edit` | `<target>/*` | allow | The assigned `CARGO_TARGET_DIR`, when linked. |
| 9 | `shell` | `*` | allow **iff** `run_local_commands`, else deny | The capability is the grant. |
| 10 | `shell` | `git push*`, `git remote*`, `git reset*`, `git clean*`, `git checkout*`, `git switch*`, `git commit*`, `git clone*`, `git fetch*`, `git pull*` | deny | Defence in depth; string globs are not a boundary. |
| 11 | `webfetch`,`websearch` | `*` | allow **iff** `use_network`, else deny | Network follows the capability. |

Rules 7 and 8 also apply to `edit` so the agent can write its message and target output. Rule 10 is
honest about its weakness: `shell` runs as the operator and no rule list can confine it. That is the
documented difference from Codex, whose sandbox does confine writes, and it is why `run_local_commands`
is a human-approved capability.

The agent also disables `title` (an auxiliary model request that would spend a turn on naming the
session) and keeps `summary`/`compaction`. `snapshots: false` removes OpenCode's own snapshot store
from the worktree path.

## Preflight

Runs by default; `--no-preflight` skips; `--dry-run` never runs it. Each failure is one cause and one
remedy, exit 2.

1. `<opencode> --version` answers, and the line is printed.
2. `<opencode> run --help` documents every long flag the built argv uses, derived from the argv.
3. `<opencode> debug agents --standalone` with the exact env of the run:
   - exits 0;
   - lists the unique agent id with `mode: primary`;
   - the guard marker contains an `after` record with this run's nonce that omits all three worktree
     tool ids, and a `before` record that contained them (proves the plugin loaded and changed
     something);
   - a probe project plugin planted at `.opencode/plugins/agentforge-preflight-<hex>/` did **not**
     write its marker (proves project plugin discovery is off);
   - the probe file and directory are removed afterwards, and the probe `opencode.json` is written
     only when the worktree has none.
4. `<opencode> debug config --standalone` reports no source document inside the worktree.
5. With `--model provider/model[#variant]`, the model appears in `<opencode> models --standalone`
   (the catalog lists a provider's models regardless of credentials, so this catches a typo, not a
   missing key — the run's own error is still the authority).
6. The scratch directory accepts a write and the assigned target directory exists (created).

Steps 3–5 apply only to an executable that identifies as `opencode`. A stand-in used by conformance
identifies as itself, so it answers 1, 2, and the agent list without pretending to load a plugin.

## Failure hints

Printed after the untouched output, preserving OpenCode's exit status:

| Signal | Hint |
| --- | --- |
| credit balance / `401` / unauthorized | the provider has no usable credentials; run `opencode auth login` or set its key |
| `provider.invalid-request` | the provider rejected the request; check `--model` and the endpoint |
| `You must provide a message` | the prompt never reached stdin; this is a bridge defect, report it |
| no model selected / unknown model | pass `--model provider/model` or configure a default |
| `Permission denied:` | the policy refused the action; widen the task, not the bridge |

## ADRs

- ADR-0061 — OpenCode as a policy-configured bridge, and the worktree guard (new; amends ADR-0056's
  isolation wording for OpenCode).
- ADR-0058 — one kit, thin adapters, conformance as the price of first-party.
- ADR-0056 — Phase A order; policy, not sandbox, is the isolation boundary.
- ADR-0060 — the preflight obligation this bridge follows.

## Public API / CLI

```text
scripts/agents/opencode-bridge
  --opencode PATH     OpenCode executable (default: `opencode` on PATH)
  --model MODEL       provider/model[#variant] to request
  --preflight / --no-preflight   verify the environment first (default: on)
  --dry-run           print the instructions and command, run nothing, no preflight
  --self-test         check the policy, config document, decoder, and preflight rules
```

`scripts/agents/opencode_e2e.py [--opencode PATH]`: exit 0 on pass or `SKIP`, 1 on failure.

## Compatibility analysis

Additive. A new executable, a new optional harness, one registry entry, and new stub answers. No
existing bridge changes behaviour. Operators who pin an OpenCode build whose help or plugin surface
has drifted get an exit-2 preflight with the named cause instead of an unsafe run.

## Dependency analysis

Python standard library only. The guard plugin imports `@opencode/plugin` (provided by the runtime)
and `node:fs`. No npm install, no lockfile, no network. No new crate, no MSRV change.

## Expected file boundary

- `.plans/P6-M007-opencode-bridge.plan.md`, `.plans/ACTIVE`
- `docs/adr/ADR-0061-opencode-bridge-policy-and-worktree-guard.md` (new)
- `docs/adr/README.md`
- `scripts/agents/opencode-bridge` (new)
- `scripts/agents/opencode-plugins/agentforge-guard/package.json` (new)
- `scripts/agents/opencode-plugins/agentforge-guard/index.ts` (new)
- `scripts/agents/opencode_e2e.py` (new)
- `scripts/agents/conformance_stub.py`, `scripts/agents/bridge_conformance.py`
- `.github/workflows/ci.yml`
- `docs/AGENT_PROFILES.md`, `docs/MILESTONES.md`
- `PROJECT_STATE.md`, `CHANGELOG.md`

## Test-first matrix

| Check | Test | Expected |
| --- | --- | --- |
| C1 policy shape | `opencode-bridge --self-test` | the catch-all deny is first, the worktree allow is last, no rule is `ask` |
| C2 secrets | self-test | `.env` and `sub/.env` are denied for read and edit |
| C3 capability grants | self-test | `shell` follows `run_local_commands`; network follows `use_network`; both deny by default |
| C4 config document | self-test | `snapshots:false`, `title` disabled, the plugin path is absolute, project isolation is set |
| C5 forbidden argv | self-test | `--auto`, `--continue`, `--session`, `--fork`, `--file`, `--server` are refused if added |
| C6 NDJSON | self-test | `text` parts concatenate; `tool_use` is reported; `error` sets failure; non-JSON passes through |
| C7 MCP refusal | self-test | a `use_mcp_tools` task exits 2 with the cause and the remedy |
| C8 preflight | self-test | a stand-in that drops a flag, omits the agent, or reports a missing guard arg exits 2 |
| C9 conformance | `bridge_conformance.py` | all eleven existing cases pass for OpenCode; 33/33 across three bridges |
| C10 real commit | `opencode_e2e.py` | exit 0, one commit, both trailers, the file present |
| C11 real path violation | e2e | exit 4, nothing committed, the file kept |
| C12 guard | e2e | a forced `opencode_worktree_remove` in a linked worktree fails; the worktree survives |
| C13 provider failure | e2e | exit 1, one hint, nothing committed |
| C14 absent OpenCode | e2e | `SKIP`, exit 0 |
| C15 docs | validators + review | ADR-0061 matches the code; AGENT_PROFILES says policy, not sandbox |

## Implementation sequence

1. Record the design: ADR-0061, this plan, `.plans/ACTIVE`, the MILESTONES row, the ADR registry,
   and `PROJECT_STATE.md` (plan-only commit).
2. Add the guard plugin directory.
3. Add `opencode-bridge`: policy, config, argv, NDJSON, hints, preflight, self-test.
4. Teach `conformance_stub.py` `run --help` and `debug agents`; register OpenCode; reach 33/33.
5. Add `opencode_e2e.py` with the streaming stub provider and the four real-binary cases.
6. Add the CI steps and the CHANGELOG entry.
7. Run the full gate, push, confirm exact-head CI, close the milestone, tag `milestone/P6-M007`.

## Failure modes

- **A future OpenCode build renames the plugin API or the tool.** The preflight fails at the marker
  check with the remedy "pin OpenCode 2.2.x or update the guard", instead of running unguarded.
- **Plugin load fails silently.** The plugin writes its marker from `setup`; a missing `after` record
  is a preflight failure, not a warning.
- **The worktree ships its own OpenCode config or plugin.** Project discovery is off, and the
  preflight plants a probe that proves it for this build. A probe that cannot be trusted (the paths
  already exist and cannot be created) fails closed.
- **A model calls a denied tool anyway.** Denied tools return a `Permission denied` tool result; the
  model sees it and continues. The bridge's path check is the backstop.
- **`shell` writes outside the worktree.** It can, because there is no sandbox. This is documented,
  gated by the `run_local_commands` capability, and not papered over as isolation.
- **MCP becomes available later.** The refusal is a single function; enabling it is a later
  milestone with its own evidence, not a silent flip.
- **The stub provider drifts from the chat-completions stream.** The harness fails loudly; it never
  falls back to "the model said nothing".

## Documentation impact

- ADR-0061 records the policy, the guard, and the two refusals (no sandbox claim, no MCP yet).
- `docs/AGENT_PROFILES.md` gains the OpenCode profile, the policy table's operator-facing summary,
  the guard, and the measured limits.
- `PROJECT_STATE.md` and `docs/MILESTONES.md` move P6-M007 to done at closure with the evidence.
- `CHANGELOG.md` records the operator-visible addition.

## Quality gates

- `python3 scripts/agents/bridge_kit.py --self-test`
- `./scripts/agents/claude-code-bridge --self-test`
- `./scripts/agents/codex-bridge --self-test`
- `./scripts/agents/opencode-bridge --self-test`
- `python3 scripts/agents/bridge_conformance.py` (33/33)
- `python3 scripts/agents/opencode_e2e.py` (real OpenCode on this machine; `SKIP` elsewhere)
- `./scripts/gate.sh full`
- `cargo run -p xtask --locked -- validate` and `validate-plan-policy`
- `./scripts/check-text-files`
- All seven CI jobs green on the exact pushed commit

## Acceptance criteria

- [ ] `opencode-bridge` runs OpenCode headless on the shared kit with the prompt on stdin,
      `--standalone`, `--format json`, a unique `--agent`, and the bridge-built
      `OPENCODE_CONFIG_CONTENT`.
- [ ] Project configuration and project plugin discovery are disabled, and the preflight proves it
      for the installed build.
- [ ] The guard plugin removes the three worktree tools; a forced `opencode_worktree_remove` against
      an active linked worktree fails and the worktree survives.
- [ ] The policy denies secrets, questions, skills, subagents, Code Mode, and everything outside the
      worktree; `shell` follows `run_local_commands`; network follows `use_network`; no rule asks.
- [ ] A `use_mcp_tools` task exits 2 with a cause and a remedy, and no run silently lacks its tools.
- [ ] The preflight exits 2 with one cause and one remedy for a missing executable, flag drift, a
      missing agent, a guard that did not load, project config that leaked, or a bad model.
- [ ] NDJSON is parsed: `text` parts become the summary, `tool_use` is reported, an `error` event is a
      failure even at exit 0, and anything else is forwarded untouched.
- [ ] `conformance_stub.py` answers OpenCode's preflight questions and all thirty-three cases pass.
- [ ] `opencode_e2e.py` proves the commit, the exit-4 refusal, the surviving worktree, and the
      provider-failure hint against the real binary, and prints `SKIP` when it is absent.
- [ ] ADR-0061 states the finding, the mitigation, and the two refusals; no document calls the
      bridge sandboxed or fully isolated.

## Completion record

Implementation commit: pending
CI run: pending
Pages: pending
CI result: pending
Completed: pending
Notes: pending
