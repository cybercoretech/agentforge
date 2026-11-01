# ADR-0063: GitHub Copilot CLI as a sandboxed first-party bridge

- Status: Accepted
- Date: 2026-10-31
- Milestone: P6-M010
- Amends: ADR-0058

## Context

Copilot CLI 1.0.105 has headless prompt mode, an explicit tool inventory, permission denials, URL
rules, a per-session MXC command sandbox, and local stdio MCP support. Prompt mode requires
`--allow-all-tools`; that switch alone would be unacceptable, but Copilot documents that
`--available-tools` limits model exposure and `--deny-tool` takes precedence over it.

## Decision

- `copilot-bridge` decodes and commits through the shared kit. It runs `-p` with JSONL output,
  isolated `COPILOT_HOME`, `--no-custom-instructions`, disabled built-in MCP, no auto-update, no
  ask-user tool, and no session resume, project trust, add-directory, plugin, fleet, or unrestricted
  permission flag.
- The bridge always passes `--allow-all-tools` because Copilot requires it in noninteractive mode,
  but pairs it with `--available-tools` and explicit denials. File tools are limited to `read`,
  `grep`, `glob`, `create`, and `edit`; `shell`, web tools/egress, and the sole `agentforge` MCP
  server appear only for their task capabilities.
  Git mutation commands are denied. `--allow-all`, `--yolo`, unrestricted paths, and unrestricted
  URLs are never used.
- The bridge writes bridge-owned `settings.json`: sandbox enabled, no bypass, no developer-tool
  access, sandboxed local stdio MCP/LSP children, and outbound/local network disabled unless
  `use_network` is present. `--sandbox` enables the policy for the session.
- Before agent execution it verifies the executable, every policy flag, and, for a real Copilot
  identity on Linux, MXC's documented `bwrap >= 0.5.0`, `slirp4netns`, namespace and firewall
  tools, and read/write TUN device. Failure is exit 2; there is no unsandboxed fallback. The
  conformance stand-in exposes Copilot-shaped help but intentionally has a different identity, so
  it exercises flag drift without pretending to prove MXC on CI.
- The bridge supplies the AgentForge MCP gateway only as a validated bridge-owned `mcpServers`
  stdio configuration. Malformed configuration or a missing `forge` is exit 2.

## Consequences

The final Git path validation remains authoritative for every provider. Copilot's sandbox provides
the additional runtime boundary for commands, but built-in edits are documented as best effort, so
they never replace the kit's changed-path check.

An isolated `COPILOT_HOME` deliberately does not inherit user settings or user-home credentials.
`--secret-env-vars` redacts a conservative list of known sensitive environment names from shell/MCP
children and output, but it cannot discover arbitrary names or secrets stored in files. Operators
must provide a supported authentication mechanism without broadening the bridge environment.

`copilot_e2e.py` is opt-in (`AGENTFORGE_COPILOT_E2E=1`) because a real model call can spend credits;
otherwise it emits a named `SKIP` in CI.

On Blackbox, after `slirp4netns 1.3.6` met the Linux MXC prerequisite, the explicitly authorized
live proof completed a bounded in-path commit and an out-of-boundary no-commit refusal. The latter
may be denied by MXC before the shared kit observes a path violation; both layers preserve the
no-unauthorized-commit invariant.
