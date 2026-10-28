# ADR-0061: OpenCode as a policy-configured bridge, and the worktree guard

- Status: Accepted
- Date: 2026-10-28
- Milestone: P6-M007
- Amends: ADR-0056 (makes "policy, not sandbox" concrete for OpenCode and records two refusals)

## Context

ADR-0056 fixed the bridge order — shared kit, Codex, OpenCode — and said that isolation for a
bridge without a sandbox is a policy the bridge states, not a boundary the OS enforces. P6-M005 and
P6-M006 delivered the Codex half of that: a real sandbox, a preflight, and evidence. P6-M007 adds
OpenCode, which has no sandbox at all. OpenCode 2.2.0 isolates by ordered permission rules
(`action`, `resource`, `effect`, last match wins) and by nothing else.

Probing the installed build settled what the bridge can rely on, and found two things it cannot.

**It found a destructive hole.** `opencode_worktree_remove` is offered to the model by default and is
not gated by permission rules. With the active linked worktree as `directory` and `force: true`, it
deleted that worktree; `git worktree list` afterwards showed only the main checkout. A catch-all
deny, `action: "worktree"`, `"worktree_remove"`, `"worktree_*"`, `"opencode_worktree_remove"`, and
`tool.use` policies all left the tool working. A bridge cannot ship while the agent can destroy the
checkout that verification, gates, and integration depend on.

**It found that MCP tools are not usable headlessly.** A configured `mcp.servers.<name>` connects
(`mcp connected … tools=1`) but the direct tool is absent from the first model request; it appears
only after a step has already happened, and not in every configuration. Code Mode would be the other
route, and Code Mode is not acceptable: the `execute` tool documents that `fetch` is available to
the scripted runtime, so allowing it grants network access outside `webfetch`/`websearch` and outside
`use_network`. `opencode mcp list` prints `No MCP servers configured` for a configured server in this
build, so it cannot even serve as a preflight check.

## Decision

- **OpenCode runs as a policy-configured bridge, and the documentation says so.** The bridge builds
  the policy, hands it to OpenCode in `OPENCODE_CONFIG_CONTENT` (a virtual source appended last),
  and keeps it out of the worktree. It does not describe the result as a sandbox.
- **The worktree cannot loosen the policy.** `OPENCODE_CONFIG_PROJECT_DISABLE=true` removes project
  `opencode.json(c)` and project `.opencode/plugins` discovery. Both were verified by planting a
  project plugin that loads with the switch off and does not load with it on. The preflight repeats
  the proof for the installed build: it plants a probe plugin, runs `debug agents`, and requires the
  guard's marker and the absence of the probe's.
- **A bridge-owned plugin removes the worktree tools, and the preflight proves it loaded.**
  `scripts/agents/opencode-plugins/agentforge-guard/` is a local plugin directory (a single file is
  skipped by OpenCode) loaded by absolute path from the config content. In `setup` it records the
  tool inventory, removes `opencode_worktree_create`, `opencode_worktree_list`, and
  `opencode_worktree_remove` with `ctx.tool.transform`, and records the inventory again to a marker
  file keyed by a per-run nonce. With the guard loaded, the same forced call fails with `No tool
  named "opencode_worktree_remove" is currently available` and the linked worktree survives.
- **Every run is `--standalone`.** Without it `run` talks to the background service, whose config,
  plugins, and permissions are not the bridge's. The bridge never passes `--auto`, `--continue`,
  `--session`, `--fork`, `--file`, `--title`, `--thinking`, or `--server`.
- **Every rule is `allow` or `deny`; nothing asks.** A headless run has nobody to answer. The
  catch-all is deny, the worktree is allowed, secrets and non-file tools are denied, and the scratch
  and assigned target roots are the only external directories.
- **`shell` follows `run_local_commands`, and its limits are stated.** A task that holds the
  capability may run commands; one that does not gets no shell at all. A few destructive git commands
  are denied as defence in depth, but the ADR does not pretend that a string glob confines a shell:
  `shell` runs as the operator, outside the worktree, and that is the documented difference from
  Codex's sandbox.
- **A `use_mcp_tools` task is refused, not degraded.** The bridge exits 2 with the measured cause
  and the remedy (run the task with Claude Code or Codex, or wait for a build with a stable MCP
  surface). Running without `check_changes` and `run_gate` would be a capability loss presented as
  success.
- **The model's stream is parsed, not trusted.** `--format json` emits NDJSON. `text` parts are
  concatenated into the summary, `tool_use` is reported, an `error` event is a failure even when the
  process exits 0, and any non-JSON line is forwarded as the agent wrote it.

## Consequences

Positive:

- a destructive default tool is removed before the model can see it, with a preflight that fails
  loudly if a future build stops honouring the plugin;
- the policy is one reviewable document, generated from the task, and the worktree cannot add to it;
- the bridge refuses the two surfaces it cannot honour (sandbox, MCP) instead of implying them;
- OpenCode joins the conformance suite unchanged, so its commit and path contract is proven by the
  same eleven cases as the other bridges.

Trade-offs:

- the guard depends on OpenCode's plugin API; a rename is a preflight failure, which is the intended
  compatibility signal, and operators may need to pin `2.2.x`;
- `shell` is a real hole for tasks that hold `run_local_commands`, and no rule list closes it; the
  capability remains a human decision;
- sessions accumulate in the operator's OpenCode database, because the data directory also holds the
  credentials and must not be isolated;
- MCP tasks cannot run on OpenCode yet, which is a capability gap recorded here rather than hidden.

## Evidence

- `opencode v2.2.0` on this machine; `debug agents --standalone` lists the generated agent with the
  bridge's rules last; `debug config --standalone` shows the content source appended last.
- Forced removal without the guard: the linked worktree was deleted. With the guard: the call fails,
  the worktree survives, and the marker records `worktree_remove` before and its absence after.
- `read`/`grep`/`glob` outside the worktree return `Permission denied: external_directory`; `read`
  of `.env` and `sub/.env` returns `Permission denied: read`; a denied `skill` call returns
  `Permission denied: skill`.
- The project-plugin control: with project discovery on, a planted plugin's marker appears; with
  `OPENCODE_CONFIG_PROJECT_DISABLE=true`, it does not.
- `scripts/agents/opencode_e2e.py` is the repeatable form of these checks; it prints `SKIP` when
  OpenCode is not installed.
