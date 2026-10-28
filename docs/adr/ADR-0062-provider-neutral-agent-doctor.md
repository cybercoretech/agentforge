# ADR-0062: Provider-neutral agent checks in `forge doctor`

- Status: Accepted
- Date: 2026-10-28
- Milestone: P6-M008
- Relates: ADR-0056 (Phase A "provider-neutral doctor checks"), ADR-0060 and ADR-0061 (bridge
  preflight)

## Context

Phase A of the 1.0 strategy puts "provider-neutral `doctor` checks" next to the bridges. Two
different questions were being conflated:

1. **Is this specific agent usable right now?** Each bridge answers this in its own preflight:
   executable version, flags, sandbox or policy surface, model catalog, write roots. The answers are
   provider-specific by nature and cannot be moved into the core without turning the core into a
   registry of agent CLIs.
2. **Can this project's agent profile start at all?** This depends only on the profile contract in
   ADR-0043 and on the host filesystem: the executable is absolute, exists, is a regular file, is
   executable; the arguments include no missing absolute paths; the profile parses within its
   bounds. None of that requires knowing which agent the profile names.

Before this decision, `forge doctor` reported only the project files and ignored arguments, while
`agent list` failed the whole listing when one profile was broken. An operator with a broken
profile had to run one command per profile to find it, and a profile that pointed at a missing
binary produced no pre-run signal at all.

## Decision

`forge doctor [<root>] --agents` appends a provider-neutral profile report.

- **Static facts only.** Doctor reads the profile contract and the filesystem. It never executes
  the profile's executable, not even `<executable> --version`, and it never opens a network
  connection. The bridge preflight remains the authority for a live agent.
- **No provider detection.** The report contains no agent name, version, model check, sandbox
  check, or per-bridge branch. The only names are the profile IDs the operator wrote. A future
  bridge gets the same checks by implementing the same profile contract.
- **Facts, levels, and a fix.** Each finding is `ok`, `warn`, or `fail`, in the shape already used
  by `forge worker remote doctor`: `doctor <level> <name>: <detail>`, then `  fix: <remedy>` when
  the level is not ok. A summary counts profiles, failing checks, and warnings. Fail exits 1; warn
  exits 0; invalid usage exits 2.
- **Report, do not stop.** Enumeration is separated from loading. A broken profile is one failing
  check with the loader's own cause; the remaining profiles are still reported. `list` keeps its
  fail-closed behaviour by reusing the new enumeration primitive.
- **No silent scope.** The default `forge doctor` output is unchanged. `--agents` is additive, and
  the previously ignored `<root>` argument now selects the project, matching what `README.md`
  already documented.

## What is deliberately not checked

- **The executable's identity or version.** Running it is execution; a basename is not evidence.
- **A provider endpoint.** A profile has no standard endpoint field. Guessing a port or reading a
  provider config would be provider-specific and can be wrong. ADR-0056 permits a reachability
  report; it does not require one, and this milestone does not invent one.
- **The bridge's policy.** Policy is provider-specific and lives in the bridge, which already proves
  it in its preflight and in its self-test.
- **A model.** A profile selects an executable, not a model. Model availability is a bridge and
  provider concern.

## Consequences

- An operator can find a non-starting profile before creating a task, with one command and one
  deterministic report, without granting doctor any execution or network surface.
- The boundary between core diagnostics and bridge preflight stays explicit: doctor refuses a
  profile that cannot run; the bridge decides whether the agent it names can run.
- The profile contract gains an operational meaning without gaining fields. There is no format
  change and no migration.
- A broken profile now fails the doctor with exit 1. Scripts that used `forge doctor` as a
  repository-readiness probe are unaffected unless they opt into `--agents`.

## References

- `docs/DOCTOR_STATUS.md`
- `docs/AGENT_PROFILES.md`
- `crates/agentforge-adapter/src/lib.rs` (`AgentProfileStore`, `AgentProfile`)
- `docs/adr/ADR-0043-real-agent-bridge.md`
- `docs/adr/ADR-0056-agentforge-1.0-strategy.md`
- `docs/adr/ADR-0060-bridge-preflight-and-corrected-codex-finding.md`
