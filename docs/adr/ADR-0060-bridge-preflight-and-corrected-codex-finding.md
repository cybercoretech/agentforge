# ADR-0060: Bridge preflight, and a corrected Codex sandbox finding

- Status: Accepted
- Date: 2026-10-27
- Milestone: P6-M006
- Amends: ADR-0059 (corrects one consequence, adds a preflight obligation)

## Context

P6-M005 shipped the Codex bridge with two recorded gaps: no live Codex turn could be run here, and
`codex sandbox` appeared unable to re-exec a mise-installed Codex binary inside its own bwrap
namespace. Both were written into ADR-0059, the plan, `PROJECT_STATE.md`, and
`docs/AGENT_PROFILES.md` as honest limits.

Both turned out to be wrong, in instructive ways.

**The sandbox finding was an artifact of the test, not the tool.** The failing runs all used a
synthetic permission profile written as `default_permissions = "ws"` with an empty
`[permissions.ws]` table. A profile that grants nothing produces a bwrap invocation whose re-exec of
the Codex binary fails, and the only thing the user sees is:

```text
bwrap: execvp /home/..../codex: No such file or directory
```

That message names the Codex binary, so it reads as "Codex cannot run from this install path". It is
not. With a profile that actually declares filesystem permissions, the same mise-installed binary,
the same bwrap, and the same kernel run sandboxed commands without complaint. The path was never the
problem; the config was, and the diagnostic actively pointed away from it.

**A live turn was possible without credentials.** Codex's OSS path speaks the OpenAI Responses API to
a local endpoint, and it probes `/api/version` (requiring Ollama 0.13.4 or newer), `/v1/models`, and
`/api/show` before the first request. With a stub endpoint answering those and streaming
`response.*` events, `codex exec --oss --local-provider ollama` completed a real turn: it issued an
`exec_command` tool call, the command ran inside the sandbox, and the file appeared in the worktree.
Driven through `scripts/agents/codex-bridge`, the same run produced a commit whose message came from
the model's own scratch file and whose trailers named `AgentForge-Task` and `AgentForge-Agent`; a
second run that wrote outside the allowed paths exited 4 and committed nothing.

So the bridge's policy and its grants were correct, and the two "limits" were a bad config and an
untried path.

## Decision

- **Correct the record.** ADR-0059 carries a pointer to this ADR at the consequence it got wrong, and
  `PROJECT_STATE.md` and `docs/AGENT_PROFILES.md` are rewritten to state what was measured. A false
  limitation is worse than no limitation: it teaches operators to avoid a working path.
- **Every bridge that hands the agent a sandbox runs a preflight before the agent starts.** For
  Codex, `--preflight` checks, in order:
  1. the executable answers `--version`, and the version is reported;
  2. every long flag the built command actually uses still appears in `codex exec --help`, and
     `workspace-write` is still an accepted sandbox mode — the required set is derived from the
     command, so a conditional flag is checked only when this task passes it;
  3. when the executable identifies as Codex on Linux, a sandbox runtime is present: `bwrap` on
     `PATH` and a working user namespace. A stand-in used by the conformance suite identifies as
     itself, so it neither needs nor pretends to have a Codex sandbox;
  4. a configured local provider answers on the port Codex uses (Ollama ≥ 0.13.4 on `11434`, LM
     Studio on `1234`), because "no server" and "too old" are otherwise discovered after Codex has
     already been started;
  5. the scratch directory, the assigned target directory, and every `--writable-dir` exist (the
     target is created) and accept a probe write.
  A failure exits 2 with the specific reason and the remedy, before a single model token is spent.
- **The bridge creates the assigned target directory before it hands it to Codex.** A linked
  worktree's `CARGO_TARGET_DIR` does not exist yet, and passing a missing `--add-dir` is a silent
  way to lose build output.
- **Preflight is automatic.** The bridge runs it unless `--no-preflight` is given. `--dry-run` skips
  it, so instructions can be inspected on a machine without Codex.
- **Common Codex failures get an actionable hint while the exit code stays Codex's own.** A missing
  binary, an unavailable sandbox, missing credentials, and a dead local provider each add one line
  naming the next step. The contract that "anything else is the agent's own status" does not change.
- **Never fall back to an unsandboxed run.** If the sandbox cannot start, the bridge fails. Silently
  running without it would discard the policy the operator approved.
- **Check in a reproducible end-to-end harness.** `scripts/agents/codex_e2e.py` starts a stub local
  provider, generates a task prompt, runs the real bridge against the real Codex binary, and asserts
  both the commit and the path-violation refusal. With no Codex installed it reports `SKIP` and exits
  0, so CI can run it without pretending. The stub is explicitly not a model and the harness says so.

## Consequences

Positive:

- flag drift in a Codex upgrade becomes a preflight failure with a named flag, instead of a confusing
  runtime error — this is not hypothetical, since 0.165.0 removed `wire_api = "chat"` outright;
- a broken sandbox configuration is caught before the agent runs, with a message that names the
  sandbox rather than the binary;
- the live-turn claim is repeatable by anyone with Codex installed, instead of being a story;
- two false statements are removed from the documentation that operators read.

Trade-offs:

- preflight spends two short subprocess calls per task launch;
- the flag list is a compatibility surface that must be updated deliberately when Codex changes, and
  a preflight failure after an upgrade is the intended signal rather than a bug;
- the end-to-end harness proves the bridge, the sandbox, and the local-provider plumbing — not model
  quality, and it must keep saying so.
