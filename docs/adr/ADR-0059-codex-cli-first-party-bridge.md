# ADR-0059: Codex CLI as a first-party bridge

- Status: Accepted
- Date: 2026-10-27
- Milestone: P6-M005
- Amends: ADR-0058 (the kit stays agent-neutral; Codex policy lives in its bridge)

## Context

ADR-0056 puts Codex CLI first in Phase A after the shared kit, and ADR-0058 makes an end-to-end
conformance suite the price of being first-party. The kit gives the Codex bridge its prompt codec,
path boundary, changed-path discovery, commit contract, and exit codes. What remains is genuinely
Codex-specific, and Codex differs from Claude Code in four ways that matter:

1. **It owns a sandbox.** `codex exec` runs model-generated commands inside a filesystem sandbox
   chosen by `-s read-only|workspace-write|danger-full-access`, with `danger-full-access` and
   `--dangerously-bypass-approvals-and-sandbox` available on the same command line.
2. **It can create worktrees.** `codex exec --worktree` runs the session in a new Codex-managed Git
   worktree, which is a different worktree from the one AgentForge verified and prepared.
3. **It has an approval policy.** `approval_policy` decides whether a command that the sandbox
   blocks is escalated to a human. Nobody is present in a headless run.
4. **It supports local providers.** `--oss --local-provider lmstudio|ollama` runs against a model
   served on the machine, which ADR-0056 treats as a property of a bridge rather than a product
   feature.

The default failure when wiring a sandboxed agent into a worktree-owning harness is to let the agent
pick the isolation policy, or to widen the sandbox until the build works. Both turn a boundary into
a suggestion.

## Decision

- **Instructions go to `codex exec` on stdin, using `-` as the prompt argument.** The task prompt is
  never placed in argv, where it would be visible in the process table and would collide with
  argument-length limits.
- **`-C` is the bridge's current directory, which AgentForge already set to the task worktree.**
  `--worktree` is forbidden. AgentForge creates, verifies, records, and integrates task worktrees
  (ADR-0056); a Codex-created worktree would sit outside that lifecycle, and the after-the-fact path
  check would inspect the wrong tree.
- **`-s workspace-write`, and never `danger-full-access`, `--dangerously-bypass-approvals-and-sandbox`,
  or `--dangerously-bypass-hook-trust`.** `read-only` cannot do the work; `danger-full-access` is not
  a policy. The bridge asserts these absences in its self-test rather than trusting review.
- **`-c approval_policy="never"`.** In a headless run there is no human to approve an escalation, so
  the only safe answers are "run it in the sandbox" or "fail". `never` makes a blocked command fail
  with the agent's own exit status instead of waiting or silently widening.
- **Granted write roots are explicit and minimal.** The sandbox is given the scratch directory (so
  the agent can write its commit message) and the assigned `CARGO_TARGET_DIR` (so the build it was
  asked to run can write). Operators add further roots with a repeatable `--writable-dir PATH`. The
  bridge never grants `$CARGO_HOME` implicitly.
- **Network is granted only by capability.** `-c sandbox_workspace_write.network_access=true` is
  passed only when the task holds `use_network`.
- **`--ephemeral` and, by default, `--ignore-user-config`.** Sessions leave no rollout files, and an
  operator's `~/.codex/config.toml` cannot silently widen the policy the bridge just set. Auth is
  unaffected, because `--ignore-user-config` still reads credentials from `CODEX_HOME`.
  `--respect-user-config` is the explicit opt-out for custom providers.
- **`-o <scratch>/last-message.txt` is the preferred summary source, with stdout as fallback.** The
  file is written by Codex's own process, outside the sandbox; it may legitimately be absent, and
  its absence must not fail the run.
- **MCP reaches Codex through `-c mcp_servers.agentforge.command` and `.args`.** The values are
  rendered as TOML and parsed back with `tomllib` before the run, so a malformed override fails at
  exit 2 instead of confusing Codex.
- **The sandbox is defence in depth, not the boundary.** The kit's path check after the agent exits
  still decides what may be committed. A sandbox escape would be caught by the same check that
  catches an out-of-range write from any other agent.

## Consequences

Measured on this machine with `codex-cli 0.165.0`, driving `codex sandbox --permission-profile` with
an explicit filesystem profile:

- writes inside the working root succeed, and writes outside the granted roots fail with
  `Read-only file system` and a non-zero status;
- an explicitly granted external root, such as the assigned target directory, is writable;
- `$CARGO_HOME` is not writable unless granted, and `~/.cargo` therefore has to be requested
  deliberately;
- reads outside the working root are permitted, so the sandbox constrains writes, not disclosure;
- network is blocked even though the host reaches the internet, unless it is granted.

A useful corollary, also measured: `cargo metadata` and a cold-target-directory `cargo check` both
succeed with a read-only `$CARGO_HOME`, because a warm registry only needs reads. The limitation is
narrow and worth stating precisely: fetching a dependency that is not already cached requires both
`use_network` and an explicit `--writable-dir "$CARGO_HOME"`.

Positive:

- Codex runs under a policy the bridge chose, not one the agent chose;
- the failure modes are loud: a blocked write fails the command instead of being silently dropped;
- local models are reachable through a documented flag rather than a bespoke integration;
- a third-party bridge now has a worked example of mapping a real sandbox onto the kit.

Trade-offs:

- the bridge takes a position on `approval_policy` and on `--ignore-user-config`, which will
  surprise an operator whose Codex configuration does something unusual;
- a task that must fetch new crates needs two extra grants, and that is friction by design;
- the sandbox is a second policy surface to keep in step with the after-the-fact check;
- the active sandbox implementation is platform-specific, and this machine could not run a real
  Codex session at all (no credentials, and `codex sandbox` cannot re-exec a mise-installed binary
  inside its own bwrap namespace). The policy above is therefore verified by construction and by
  conformance, not by a live model run; that gap is recorded rather than papered over.
