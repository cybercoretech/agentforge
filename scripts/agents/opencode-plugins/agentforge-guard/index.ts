// The AgentForge guard for OpenCode (ADR-0061).
//
// OpenCode 2.2.0 offers `opencode_worktree_remove` to the model, and that tool is not gated by
// permission rules or `policies`: with `force: true` it deletes the worktree it is pointed at. When
// the bridge runs inside an AgentForge worktree, the model could therefore destroy the checkout that
// path verification, gates, and integration depend on.
//
// This plugin removes the worktree tools before a request is built. A future OpenCode that changes
// the tool names, or stops honouring the transform, makes the bridge's preflight fail: the marker
// must contain an `after` record that no longer lists them.
//
// The marker is opt-in. The bridge sets AGENTFORGE_GUARD_MARKER to a file outside the worktree and
// AGENTFORGE_GUARD_NONCE to a per-run value; the preflight reads both back.

import { Plugin } from "@opencode/plugin"
import { appendFileSync } from "node:fs"

const GUARDED = ["opencode_worktree_create", "opencode_worktree_list", "opencode_worktree_remove"]

export default Plugin.define({
  id: "agentforge.guard",
  async setup(ctx) {
    const marker = process.env.AGENTFORGE_GUARD_MARKER
    const nonce = process.env.AGENTFORGE_GUARD_NONCE ?? ""
    const record = (stage: string, tools: readonly string[]) => {
      if (!marker) return
      appendFileSync(marker, JSON.stringify({ stage, nonce, version: ctx.app.version, tools }) + "\n")
    }
    record("before", (await ctx.tool.list()).map((tool) => tool.id))
    await ctx.tool.transform((editor) => {
      for (const id of GUARDED) editor.remove(id)
    })
    record("after", (await ctx.tool.list()).map((tool) => tool.id))
  },
})
