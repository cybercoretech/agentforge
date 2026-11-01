# Plan: P6-M010 — GitHub Copilot CLI bridge

Status: Approved
Milestone: P6-M010
Created: 2026-10-31

## Goal

Add a first-party AgentForge bridge for GitHub Copilot CLI. It must preserve the shared prompt,
task-boundary, commit, and exit-code contract while enforcing Copilot's documented noninteractive
permission and sandbox controls.

## Scope

- Add one thin bridge on `bridge_kit.py`.
- Run it through the existing adversarial conformance suite.
- Add Copilot-specific preflight, command construction, failure reporting, and self-tests.
- Add an optional real-CLI E2E harness that skips honestly when credentials or prerequisite sandbox
  support are absent.
- Register the bridge's self-test, conformance coverage, and E2E harness in CI.
- Document profiles, policy limits, model selection, MCP support or refusal, and local validation.
- Record the provider-policy boundary in a new ADR.

## Provider policy requirements

### GitHub Copilot CLI

- Run headlessly with `-p`, JSONL output, a fresh session, no custom workspace configuration, and
  Copilot command sandboxing.
- Use `--available-tools`, explicit allow/deny rules, path restrictions, URL restrictions, and
  secret-environment redaction. Never use `--allow-all`, `--yolo`, or `--allow-all-paths`.
- Disable built-in and workspace MCP servers. Give the AgentForge MCP gateway only to tasks with
  `use_mcp_tools`, through a bridge-owned temporary configuration.
- Refuse when sandbox prerequisites or the documented permission surface are unavailable.

## Non-goals

- No Gemini CLI or Cursor Agent bridge in this milestone; their provider policy surfaces require
  dedicated compatibility milestones before AgentForge can make first-party guarantees.
- No bridge for Crush, Grok, Antigravity, Hermes Agent, or any other selected CLI in this milestone.
- No general provider registry, automatic agent discovery, or profile-format change.
- No claim that a provider policy is an operating-system sandbox unless the provider's documented
  sandbox is enabled and its boundary is verified.
- No external MCP server, browser, computer-use, fleet/subagent, session-resume, or provider
  configuration authority.

## Expected file boundary

- `.plans/P6-M010-selected-agent-bridges.plan.md`, `.plans/ACTIVE`
- `docs/adr/ADR-0063-copilot-cli-bridge-policy.md`, `docs/adr/README.md`
- `scripts/agents/copilot-bridge`
- `scripts/agents/copilot_e2e.py`
- `scripts/agents/bridge_conformance.py`
- `.github/workflows/ci.yml`
- `docs/AGENT_PROFILES.md`, `docs/AGENT_ADAPTERS.md`, `docs/CI.md`
- `README.md`, `PROJECT_STATE.md`, `docs/MILESTONES.md`, `CHANGELOG.md`

## Test-first matrix

| Check | Coverage |
| --- | --- |
| Shared contract | Existing conformance cases pass for the Copilot bridge. |
| Unsafe flags | The self-test asserts unrestricted approval, unrestricted paths, URLs, session resume, plugin, and workspace configuration flags are absent. |
| Capability policy | Self-tests prove shell, network, MCP, path, and secret policy follow the task or fail closed. |
| Preflight | Stubs cover missing executables, changed/removed flags, missing sandbox prerequisites, malformed MCP configuration, and unavailable capability enforcement. |
| Real execution | The E2E harness proves a bounded in-path commit and a path-violation refusal, or prints a specific `SKIP` and exits zero when its optional live prerequisite is unavailable. |
| Documentation | Validators and review confirm profiles and claimed policy boundaries match the command builders. |

## Implementation sequence

1. Commit this plan and active pointer separately from implementation.
2. Verify the plan-only checkpoint locally and in exact-head CI.
3. Implement Copilot; run its self-test and conformance suite.
4. Add its E2E harness and CI coverage.
5. Update the ADR and operator documentation.
6. Run the full gate, push, confirm exact-head CI and Pages, then close and tag the milestone.

## Quality gates

- `python3 scripts/agents/bridge_kit.py --self-test`
- `python3 scripts/agents/bridge_conformance.py`
- Provider bridge `--self-test` commands
- `./scripts/gate.sh full`
- Exact-head `AgentForge CI` and `AgentForge Pages` runs

## Acceptance criteria

- [ ] Copilot has a provider-specific, fail-closed bridge with no unrestricted approval, unmanaged
      worktree, or unreviewed project configuration path.
- [ ] The bridge passes the shared adversarial conformance suite.
- [ ] Copilot's sandbox and permission preflight produces an actionable exit 2 before an
      agent runs when the planned policy cannot be established.
- [ ] Network, shell, MCP, and writable paths follow task capabilities.
- [ ] Real-CLI E2E coverage proves a commit and an out-of-boundary refusal when prerequisites are
      available; a missing optional live prerequisite is a named skip, never false success.
- [ ] Documentation and CI match the implemented bridge surface.

## Completion record

Implementation commit: pending
CI run: pending
Pages: pending
Completed: pending
Notes: pending
