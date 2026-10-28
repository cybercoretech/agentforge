# ADR-0058: One bridge kit, thin per-agent bridges, and a conformance gate

- Status: Accepted
- Date: 2026-10-27
- Milestone: P6-M004
- Amends: none (extends ADR-0043)

## Context

ADR-0043 placed a bridge between the process adapter and a real coding agent. The bridge decodes
the length-delimited `agentforge-task-prompt-v1` document, translates it into natural-language
instructions, verifies every changed path against the task's allowed and forbidden paths after the
agent exits, and commits the result so the repository's own pre-commit gate runs on it.

That design is sound, and after two years of dogfooding it is the part of the harness that has
actually been exercised under pressure: it has implemented milestones on AgentForge itself, run as
a remote worker over GhostPort, and been hardened by findings 22 and 23.

It is also a single 535-line Python file with one implementation, and ADR-0056 commits to two more
first-party bridges (Codex CLI, then OpenCode) plus third-party ones built on a kit. That produces
a specific, foreseeable failure: every new bridge copies the checks that matter — strict decoding,
boundary verification including rename sources, exit-code semantics, commit trailers — and one copy
drifts. A drifted copy is a security boundary that only looks like one, and the drift would be
invisible, because each bridge is tested only by its own `--self-test`.

There is a second, narrower gap. The Python decoder round-trips against the Python encoder in
`--self-test`. The real producer is `render_task_prompt` in Rust. If the two implementations
disagree, every round-trip test still passes and the bridge rejects real prompts, or accepts
malformed ones, with no failing test anywhere.

## Decision

- **Split the bridge into an agent-neutral kit and a thin per-agent adapter.** The kit owns the
  prompt codec, the path-boundary rules, changed-path discovery (including rename and copy
  sources), worktree target-directory isolation, the commit with task trailers, and the exit-code
  contract. A bridge supplies only what is genuinely agent-specific: the instruction text, the
  argv, the permission or sandbox policy, the MCP configuration format, and the co-author trailer.
- **Keep the exit-code contract and the commit shape frozen.** `0` committed, `2` invalid input or
  configuration, `3` no changes, `4` path violation, `5` commit or gate failure, anything else the
  agent's own status. The trailers `AgentForge-Task` and `AgentForge-Agent` remain. This is a
  compatibility surface, not an implementation detail.
- **Pin the prompt format with a shared fixture.** One canonical `agentforge-task-prompt-v1`
  document is checked in. A Rust test asserts that `render_task_prompt` produces exactly those
  bytes, and the kit's self-test asserts that its decoder reads exactly those bytes and re-encodes
  them identically. Neither side can drift without a failure.
- **A bridge is first-party only if it passes the conformance suite.** The suite drives each bridge
  end to end against a stub agent in a throwaway repository, covering in-boundary work, an
  out-of-boundary write, a forbidden path, a rename out of bounds, an agent failure, no changes, a
  missing commit message, a dry run, and a failing pre-commit hook. Third-party bridges may be
  contributed, but they enter the tree against the same suite.
- **The kit is a library, not a runner.** It does not choose the agent, own the CLI surface, or
  install a default policy. Each bridge remains an independently executable script that a reviewed
  profile can point at.

## Consequences

Positive:

- the checks that constitute the security boundary exist once and are tested once;
- a kit fix reaches every bridge, instead of being re-derived per bridge;
- a contributor writing a third bridge writes instructions and argv, not a prompt decoder;
- the Rust encoder and the Python decoder are now mutually pinned, closing a real gap;
- conformance gives review a concrete, executable definition of "this bridge is safe enough".

Trade-offs:

- the kit becomes a compatibility surface with its own stability obligation;
- a bridge can no longer be a single copyable file, so it depends on the kit being present in the
  same checkout;
- the conformance suite builds throwaway git repositories, which is slower than unit tests and
  must be confined to one CI job;
- abstractions can hide policy. The kit must therefore keep the agent-specific decisions visibly in
  each bridge, and must not grow a "smart" default that quietly applies to agents it does not fit.
