# ADR-0056: AgentForge 1.0 strategy

- Status: Accepted
- Date: 2026-10-27
- Milestone: none (strategy ADR; implementation is planned separately)

## Context

AgentForge at `0.3.1-alpha` is technically sound: seventeen crates, 333 passing tests, a clean
`clippy -D warnings` and `fmt --check`, an enforced plan-first policy, an integrity-chained audit
log, deterministic worktrees, and a proven self-hosting loop in which Claude Code has implemented
milestones through the harness. The governance model is the product, and it is real.

A 1.0 strategy memo proposed four pillars: first-class local models, two to four more agent
bridges, an operator TUI, and cyberterm integration. Reviewing that memo against the repository
and its GitHub state showed that it correctly identifies adoption friction but misstates current
capability in five places, and omits the largest defect.

The evidence gap is the defect:

- CI has run on the canonical repository exactly once, manually, for the Pages site
  (2026-10-17). Neither `ci.yml` nor `release.yml` has ever produced a run there, although three
  pushes to `main` have landed since the workflows were registered.
- The releases API returns an empty list. The `v0.3.1` tag page carries only GitHub's automatic
  source archives: no binaries, no SBOM, no provenance, no attestations.
- `README.md` links "attested builds and SBOM" to that empty tag page.
- The completion-evidence section of `PROJECT_STATE.md` cites roughly thirty CI run IDs
  (`3610…`–`3621…`) that resolve to `404` on both the canonical and the retired predecessor
  repository. They record work that really happened, but a reader cannot verify any of it.

A project whose stated source of truth is durable evidence is currently shipping evidence that a
skeptical reader cannot check. No amount of new capability repairs that.

The operator has decided that 1.0 serves two audiences at once: the maintainer's daily driver, and
a new Linux operator evaluating the harness for the first time.

## Decision

**Evidence before features.** Phase 0 restores verifiable evidence and ships nothing new:

- resolve the `push` trigger defect so that `main` is gated by CI;
- publish a real `0.4.0` whose archives carry binaries, an SBOM, and provenance that
  `gh attestation verify` accepts against `cybercoretech/agentforge`;
- annotate historical run IDs as predecessor-repository records rather than presenting them as
  canonical evidence (history is annotated, not rewritten);
- reconcile the documentation claims that no longer match the code.

Until Phase 0 completes, no new capability work is approved.

**Bridge policy: one kit, two first-party bridges.**

- Extract the proven pattern of `scripts/agents/claude-code-bridge` — strict prompt decode,
  bounded tools, post-run path verification, bridge-owned commit, evidence, stable exit codes —
  into a shared bridge kit with a conformance suite.
- Keep Claude Code as the reference bridge. Add **Codex CLI** and **OpenCode** as the only
  first-party bridges for 1.0.
- Codex is first because it is the least glue and the most honest: `codex exec` reads the
  `agentforge-task-prompt-v1` document from stdin with `-`, `-C` binds the working root to the
  managed worktree, and `--oss --local-provider ollama|lmstudio` gives local inference without a
  wrapper.
- OpenCode is second because it is MIT, provider-agnostic (`--model provider/model`), MCP-native,
  and already supports any OpenAI-compatible endpoint.
- Further bridges are third-party contributions built on the kit, not maintenance carried by the
  core team. Proprietary cloud-only CLIs are not first-party bridges; adding them contradicts the
  local-first positioning.

**Local models are a bridge property, not a core discovery feature.** `ollama run` is not a coding
agent: it cannot edit files or commit. The useful unit is an agent CLI pointed at a local
endpoint, which is exactly what the Codex and OpenCode bridges provide. Core will not grow an
Ollama daemon, model downloader, or provider registry. `forge doctor` may report whether a
configured endpoint is reachable, provider-neutrally.

**AgentForge owns the worktree.** A bridge must never let an agent create its own. `codex
--worktree` is forbidden, and an integration that launches an agent inside a cyberterm session
must pass `--no-worktree` so that `.forge/worktrees/<task>` remains the single managed workspace.

**Isolation stays policy, stated honestly.** The boundary is capability policy, managed worktrees,
post-run path verification, and gates. Where an agent offers its own OS sandbox
(`codex -s workspace-write`), the bridge may enable it as defense in depth, but no release note
may describe AgentForge as a sandbox. Building a namespace or seccomp sandbox is out of scope for
1.0 and remains an advanced, optional capability.

**The HUD remains the single read-only projection.** Any richer operator view is a renderer over
a stable, versioned HUD output, adds no state, and performs no writes. A TUI that diverges from
durable state is worse than no TUI.

**Cyberterm consumes evidence; it does not co-own worktrees.** Cyberterm already implements agent
sessions, worktrees, flight logs, and merge/discard, so the integration work is a worktree
ownership decision plus a read-only evidence surface, not a new feature. It is post-1.0.

**1.0 declares a small stable surface.** The stability contract names the operator verbs that are
frozen and marks everything else explicitly unstable. Alpha caveats stop being a blanket excuse
for every command.

**Phase order.**

- Phase 0 — evidence: CI on `main`, a verifiable `0.4.0`, documentation reconciliation, audit
  append-lock fairness (dogfooding finding 23).
- Phase A — bridge kit, Codex, OpenCode, provider-neutral `doctor` checks, a verified quickstart.
- Phase B — HUD output schema and renderer, cyberterm evidence surface, top first-run frictions
  from `docs/DOGFOODING.md`.
- Phase C — 1.0: frozen CLI surface, migration notes from 0.3.x, a security model page that says
  policy rather than sandbox, and attested releases.

**Non-goals for 1.0.** A general personal assistant; computer use or browser automation as core;
a required cloud control plane; a guarantee of agent safety beyond workflow and policy; a
first-party bridge for every popular CLI; a built-in OS sandbox.

## Consequences

- 1.0 ships later in feature terms than the memo proposed: no TUI headline, no isolation work, and
  no Ollama discovery. It ships verifiable instead, which is the only thing that makes the
  positioning credible.
- Every first-party bridge becomes a security boundary with a conformance obligation, including
  the adversarial "writes outside allowed paths" case. Two well-tested bridges are preferred to
  five thin ones.
- Documentation becomes testable: claims about releases, CI, and capabilities must resolve to a
  commit, a tag, or a run in the canonical repository, and drift is treated as a defect.
- Local-model support is delivered as bridge acceptance criteria, so it cannot ship ahead of a
  real run through the harness.
- Contributions of additional bridges are welcome and cheap once the kit exists, which converts a
  maintenance burden into a contribution path.

## References

- `docs/adr/ADR-0011-agent-adapter-execution-boundary.md`
- `docs/adr/ADR-0043-real-agent-bridge.md`
- `docs/adr/ADR-0015-capability-policy.md`
- `docs/adr/ADR-0010-managed-worktree-lifecycle.md`
- `docs/adr/ADR-0053-keyless-release-provenance.md`, `ADR-0054-release-sbom.md`
- `docs/DOGFOODING.md` (findings 1–23)
- `docs/OPERATIONS.md`, `docs/REGISTRY.md`
