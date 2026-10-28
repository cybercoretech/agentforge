# Plan: P6-M008 — Provider-neutral `forge doctor --agents`

Status: Approved
Milestone: P6-M008
Created: 2026-10-28

## Goal

Give an operator one bounded, read-only way to answer "can this project's agent profiles start?"
before a task is created. `forge doctor [<root>] --agents` enumerates `.forge/agents/*.conf` in
deterministic order and reports, for each profile, whether the profile parses and whether the facts
it names exist on this host: the executable and its permission bit, every absolute `argument=`
path, and whether the profile opts out of the bridge's own preflight.

The check is **provider-neutral**. It reads the profile contract and the filesystem; it names no
agent, branches on no bridge, runs no executable, starts no network request, and writes nothing. A
profile that cannot start is a failing check with one named fix and exit 1, not a panic and not a
silently skipped profile. This is the Phase A "provider-neutral doctor checks" item in ADR-0056;
the per-bridge preflight remains the authority for a specific agent.

## Non-goals

- No per-provider logic: no Claude/Codex/OpenCode names, no version probing, no `--version`,
  `run --help`, model catalog, sandbox, or policy checks. Those stay in each bridge's preflight.
- No execution of any kind. Doctor does not launch the profile's executable, not even to ask its
  version. A test proves this with a profile whose executable would mutate the filesystem.
- No automatic repair, no profile rewriting, no task transition, no worktree or audit mutation.
- No change to the profile format, `agent list|validate|inspect`, the bridge kit, or any bridge.
- No change to the default `forge doctor` output. `--agents` appends a section; without it the
  command prints exactly what it printed before.
- No new dependency, no MSRV change, no workflow change.

## Context

`forge doctor` today prints five lines about the current directory and exits 0
(`print_doctor` in `crates/agentforge-cli/src/main.rs`). It accepts no arguments and ignores any
that are given, even though `README.md` already tells operators to run `forge doctor <root>`. The
remote-worker path has the report shape this milestone reuses: `DoctorLevel` (`ok|warn|fail`),
one line per check, an indented `fix:` line, and a summary, with exit 1 when a check fails
(`remote_doctor` in `crates/agentforge-daemon/src/worker_api.rs`).

Profiles are stricter than doctor can afford to be. `AgentProfileStore::load` fails the whole load
when the executable is missing, and `list` fails the whole listing when one profile is broken. That
is correct for launching — a run must never use a broken profile — but it is wrong for a
diagnostic, which must report the broken profile and keep going. The adapter therefore gains one
primitive: enumerate profile IDs without loading them. `list` is reimplemented on top of it, so
discovery stays in one place and keeps its exact behaviour.

The bridge kit already answers "is this specific agent usable right now?" with a preflight
(ADR-0060, ADR-0061). Doctor answers the earlier, cheaper question: "is this profile internally
consistent and portable on this host?" The two do not overlap and neither replaces the other.

## Design

### Surface

```text
forge doctor                     unchanged: the five existing lines, exit 0
forge doctor <root>              the same lines for <root> (the documented form, now honoured)
forge doctor [<root>] --agents   the same lines, then the agent-profile section
forge doctor <bogus-option>      usage on stderr, exit 2
```

At most one non-flag root and at most one `--agents` are accepted; any other ordering is the same
set. `<root>` defaults to `.`. `--agents` never changes the system lines; it appends.

### Checks, all static

| Name | Level | What it asserts | Fix when it fails |
| --- | --- | --- | --- |
| `agents` | ok | `.forge/agents` is absent or holds no `.conf` file | — |
| `agents` | fail | the directory can be listed | make it readable or remove it |
| `profile <id>` | ok | the profile loads: version, keys, bounds, absolute existing executable, regular file | — |
| `profile <id>` | fail | the profile loads; the detail is the loader's own cause | correct `.forge/agents/<id>.conf` |
| `profile <id> executable` | ok/fail | the executable bit is set where the platform models one | `chmod +x` it or point at the right binary |
| `profile <id> argument` | fail | every absolute `argument=` path exists | correct the argument or install the file |
| `profile <id> preflight` | warn | `--no-preflight` is not among the arguments | remove `--no-preflight` from the profile |

A healthy profile produces one `ok` line; only a problem adds a second line. Warnings do not fail
the command. The section ends with
`agents doctor: N profile(s), F failing check(s), W warning(s)`; the exit status is 1 when `F > 0`
and 0 otherwise.

`profile <id>` uses the loader, so a missing executable, an invalid profile ID, a symlinked profile
file, a repeated key, and an out-of-range value all become one failing check with the loader's
message. The fix line names the profile file rather than repeating the message.

The directory is not required to exist. A project with no profiles is a normal, healthy state; the
check prints `no agent profiles configured` and exits 0.

### Why these checks and not more

- **Absolute arguments must exist.** A profile copied from another checkout commonly names
  `/…/claude-code-bridge`, `--forge`, `/…/forge`, or a model path. `remote_doctor` already fails on
  a missing absolute argument; doctor applies the same portable-profile rule locally.
- **No "is this a bridge" inference.** The basename of an executable is not evidence of a
  provider, and guessing would make doctor fail for a perfectly valid profile.
- **No version probing.** Running `<executable> --version` is still running the executable. It
  could be the wrong binary and it violates the read-only promise. The bridge preflight owns that
  question.
- **No network probe.** ADR-0056 allows doctor to report whether a configured endpoint is
  reachable, provider-neutrally, but a profile does not declare an endpoint in a standard form.
  Guessing is worse than not checking.

## Architecture placement

```text
crates/agentforge-adapter/src/lib.rs   AgentProfileStore::ids (discovery without loading)
crates/agentforge-cli/src/main.rs      doctor argument parsing, checks, report, exit status
crates/agentforge-cli/tests/agent_commands.rs   black-box CLI coverage
docs/DOCTOR_STATUS.md                  the operator contract for the new section
docs/AGENT_PROFILES.md                 where the check fits before a run
docs/adr/ADR-0062-...md                the provider-neutral decision
```

The adapter stays presentation-free: `ids` returns sorted strings and nothing else. The CLI owns
levels, wording, fixes, summary, and exit status.

## Data flow

```text
forge doctor [<root>] --agents
        │
        ├── print_doctor(root)                 five existing lines, unchanged
        │
        └── AgentProfileStore::ids()
                │ NotFound ──▶ ok: no agent profiles configured
                │ error    ──▶ fail agents + fix
                ▼
            for each id (sorted)
                ├── load(id)
                │     Err ──▶ fail profile <id> (cause + fix)
                │     Ok  ──▶ ok profile <id> (executable, counts, limits)
                │              ├── executable bit ──▶ ok|fail
                │              ├── absolute arguments exist ──▶ fail when missing
                │              └── --no-preflight ──▶ warn
                ▼
            agents doctor: N profile(s), F failing check(s), W warning(s)
            exit 1 if F > 0, else 0
```

Nothing in this path writes, spawns, or connects.

## Invariants

- Doctor never executes a profile's executable and never opens a socket.
- Doctor never mutates the project, `.forge/agents`, the worktree, the task graph, or the audit log.
- Profile output order is the profile ID order, which is byte-sorted and stable.
- Every non-ok line has a non-empty `fix:` line.
- The default `forge doctor` output is unchanged, including for a root with profiles.
- A broken profile makes the report fail; it never disappears from the report.
- No document calls this a sandbox, a live-agent test, or a substitute for bridge preflight.

## Public API / CLI

```text
forge doctor [<root>] [--agents]
```

- `<root>`: project root to observe; defaults to the current directory.
- `--agents`: append the agent-profile report.
- Exit: 0 when no check fails, 1 when at least one fails, 2 for invalid usage.

No other command, flag, file format, or library signature changes. `AgentProfileStore::ids` is
additive; `list` keeps its behaviour and its errors.

## Compatibility analysis

Additive. `forge doctor` with no arguments is byte-identical. `forge doctor <root>` starts doing
what `README.md` already documented instead of ignoring the argument; that is the only behaviour
change and it is a fix. Scripts that pass an unknown flag to doctor previously had it silently
ignored and now get exit 2, which is the correct fail-closed result. No existing agent profile
changes meaning, and no profile that loads today is rejected by doctor unless its executable bit is
missing or an absolute argument path is absent — facts that would fail at launch anyway.

## Dependency analysis

No new crate and no new dependency. The CLI already depends on `agentforge-adapter` and
`agentforge-daemon`; `DoctorLevel` is reused from the daemon so the labels stay identical across
doctor surfaces. The permission-bit check is `cfg(unix)` with `std::os::unix::fs::PermissionsExt`,
already used by `worker_api.rs`; on other platforms the line reports that the platform does not
model the bit. No network, no subprocess, no unsafe.

## Expected file boundary

- `.plans/P6-M008-forge-doctor-agents.plan.md`, `.plans/ACTIVE`
- `docs/adr/ADR-0062-provider-neutral-agent-doctor.md` (new)
- `docs/adr/README.md`
- `crates/agentforge-adapter/src/lib.rs`
- `crates/agentforge-cli/src/main.rs`
- `crates/agentforge-cli/tests/agent_commands.rs`
- `docs/DOCTOR_STATUS.md`, `docs/AGENT_PROFILES.md`, `docs/MILESTONES.md`
- `README.md`, `PROJECT_STATE.md`, `CHANGELOG.md`

## Test-first matrix

| Check | Test | Expected |
| --- | --- | --- |
| C1 healthy profile | CLI test | exit 0; `doctor ok profile <id>:` with executable and counts |
| C2 missing executable | CLI test | exit 1; `doctor fail profile <id>:` names the path; one `fix:` line |
| C3 permission bit | CLI test (unix) | a regular non-executable file fails with `chmod +x` guidance |
| C4 absolute argument | CLI test | a missing `/…/path` argument fails and names it; a present one passes |
| C5 preflight opt-out | CLI test | `--no-preflight` is a `warn` and exit stays 0 |
| C6 invalid profile | CLI test | an invalid ID and a malformed key each produce a `fail` named by profile, not a panic |
| C7 no profiles | CLI test | absent and empty `.forge/agents` are `ok`, exit 0 |
| C8 nothing runs | CLI test | a profile whose executable is `forge init <empty-dir>` leaves the directory empty |
| C9 unchanged default | CLI test | `forge doctor` prints no `profile` line with profiles present |
| C10 root and usage | CLI test | `<root>` is honoured; `--bogus` exits 2; repeated `--agents` exits 2 |
| C11 discovery primitive | adapter test | `ids` returns sorted `*.conf` stems, ignores other files, and tolerates an absent directory |
| C12 docs | validators + review | ADR-0062 matches the code; DOCTOR_STATUS and AGENT_PROFILES agree; no provider is named |

## Implementation sequence

1. Record the design: ADR-0062, this plan, `.plans/ACTIVE`, the MILESTONES row, the ADR registry,
   and `PROJECT_STATE.md` (plan-only commit).
2. Add `AgentProfileStore::ids` and reimplement `list` on it; add the adapter test.
3. Add doctor argument parsing and the check set in the CLI.
4. Add the black-box CLI tests, including the non-execution proof.
5. Update `docs/DOCTOR_STATUS.md`, `docs/AGENT_PROFILES.md`, `README.md`, and `CHANGELOG.md`.
6. Run the full gate, push, and confirm exact-head CI is green on all seven jobs.
7. Close the milestone, record evidence, and tag `milestone/P6-M008`.

## Failure modes

- **A profile is broken in a way `load` reports only as one string.** That is acceptable: the
  check fails, the report continues with the remaining profiles, and the fix names the file. The
  loader's message is the cause; doctor does not invent a second parser.
- **An absolute argument is not a path** (for example a model identifier that happens to start
  with `/`). It will fail as missing. That is the same rule `remote_doctor` applies, and a profile
  with such an argument is not portable.
- **A platform does not model an executable bit.** The line reports `ok` with that fact instead of
  pretending to have checked it, and the test for C3 is Unix-only.
- **The directory is unreadable.** Doctor fails the `agents` check and does not claim there are no
  profiles.
- **A future profile format adds a field.** This plan adds none. If one is added later, doctor
  inherits it through `load` and the summary line, not through a second parser.
- **An operator expects doctor to prove the agent works.** It does not; the bridge preflight and a
  real run do. The docs say this plainly.

## Documentation impact

- ADR-0062 records the provider-neutral decision: static facts only, no provider detection, no
  execution, no network, and why that is the right division with bridge preflight.
- `docs/DOCTOR_STATUS.md` gains the section contract, levels, names, summary, and exit status.
- `docs/AGENT_PROFILES.md` adds the pre-run check next to the existing inspection commands.
- `README.md`'s "Agent did not start" row names `forge doctor <root> --agents`.
- `CHANGELOG.md` records the operator-visible addition.
- `PROJECT_STATE.md` and `docs/MILESTONES.md` move P6-M008 to done at closure with evidence.

## Quality gates

- `cargo test -p agentforge-cli --test agent_commands`
- `cargo test -p agentforge-adapter`
- `./scripts/gate.sh full`
- `cargo run -p xtask --locked -- validate`
- `cargo run -p xtask --locked -- validate-plan-policy`
- `./scripts/check-text-files`
- All seven CI jobs green on the exact pushed commit

## Acceptance criteria

- [ ] `forge doctor [<root>] --agents` enumerates `.forge/agents/*.conf` in deterministic order and
      reports every profile, including broken ones, without stopping at the first failure.
- [ ] Every check is static: the command never runs the profile's executable, never opens a
      network connection, and never writes to the project, worktree, task graph, or audit log.
- [ ] A failing check produces one cause and one named fix and exits 1; warnings exit 0; invalid
      usage exits 2.
- [ ] An absent or empty profile directory is `ok` and exits 0.
- [ ] The default `forge doctor` output is unchanged, and `forge doctor <root>` observes that root.
- [ ] No provider or bridge is named in the code or the output; the decision is recorded in
      ADR-0062 and the check set is derived from the profile contract alone.
- [ ] Tests cover a healthy profile, a missing executable, a missing permission bit, a missing
      absolute argument, `--no-preflight`, an invalid profile, no profiles, the non-execution
      proof, the unchanged default, root handling, and usage errors.
- [ ] `DOCTOR_STATUS.md`, `AGENT_PROFILES.md`, `README.md`, and `CHANGELOG.md` match the behaviour;
      the full gate, `xtask validate`, `validate-plan-policy`, and `check-text-files` are green.

## Completion record

Implementation commit: pending
CI run: pending
Pages: pending
CI result: pending
Completed: pending
Notes: pending
