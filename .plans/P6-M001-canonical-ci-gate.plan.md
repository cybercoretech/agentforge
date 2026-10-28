# Plan: P6-M001 — Restore the canonical CI gate

Status: Complete
Milestone: P6-M001
Created: 2026-10-27

## Goal

Make `main` gated by CI again. After this milestone, a normal push to `main` creates an AgentForge
CI run for that exact commit without manual dispatch, and the run is green on all seven jobs. Tag
pushes must likewise produce a release run, verified in P6-M003.

## Non-goals

- No change to what CI checks; the seven jobs and their steps stay as they are.
- No branch-protection reconfiguration.
- No new workflows, unless re-registration fails and a fresh workflow file is the only remedy.
- No feature work. This milestone exists to make later evidence checkable.

## Context

The canonical repository was created on 2026-10-17T04:35:58Z by pushing the migrated history. All
three workflows registered five seconds later.

Observed on 2026-10-27:

- `workflow_dispatch` of `ci.yml` on `main` worked: run `40627714988`, **seven of seven jobs green**,
  including the Windows job that P2-M023 previously fixed.
- `push` events: **zero** runs, despite four pushes to `main` (2026-10-17, 2026-10-24, 2026-10-28
  twice, the last of which updated `pushed_at`).
- `pull_request` events: zero runs.
- Actions is enabled at repository level (`enabled: true`, `allowed_actions: all`,
  `sha_pinning_required: false`). The repository is public, not a fork, not archived, default branch
  `main`. `ci.yml` is plain ASCII with a valid `on:` block.

A healthy workflow that runs on dispatch but never on `push` or `pull_request` is the signature of
triggers that were never indexed when the workflow files arrived with the migration push. The
standard remedy is to modify the workflow file in a normal push so GitHub re-reads and registers the
`on:` block.

## Architecture placement

CI configuration only: `.github/workflows/`. No crate, no runtime behavior, no public API.

## Data flow

Git push → GitHub event index → workflow registration → runner assignment → seven jobs → check
runs on the commit.

## Invariants

- The gating definition of CI does not change: same jobs, same steps, same required evidence.
- A fix must not depend on a human remembering to dispatch a run.
- Every recorded run ID in project documentation must resolve in this repository.

## ADRs

- ADR-0056 — evidence before features; Phase 0.

## Public API / CLI

None.

## Compatibility analysis

None. Workflow files are not part of the crate surface.

## Dependency analysis

No new dependencies. No action version changes.

## Expected file boundary

- `.github/workflows/ci.yml` — minimal non-behavioral change to force registration.
- `.github/workflows/release.yml` — same treatment, so tag triggers are re-registered before
  P6-M003.
- `.plans/P6-M001-canonical-ci-gate.plan.md`, `.plans/ACTIVE`.
- `docs/MILESTONES.md`, `PROJECT_STATE.md`, `CHANGELOG.md` when the milestone closes.

## Test-first matrix

| Check | Method | Expected |
| --- | --- | --- |
| T1 push trigger | push a commit to `main`, then query the runs API for that `head_sha` | exactly one `push` run exists for the pushed commit, with no dispatch |
| T2 gating | inspect the run from T1 | seven of seven jobs green |
| T3 PR trigger | open a pull request from a scratch branch | one `pull_request` run for the head SHA |
| T4 dispatch preserved | `workflow_dispatch` of `ci.yml` | accepted, run created |
| T5 tag trigger | deferred to P6-M003, proven by the `v0.4.0-rc.1` tag | release run created by the tag push |

T1–T4 are observations against the GitHub API, not automated tests; the repository has no harness
for its own trigger configuration. Each observation is recorded in `PROJECT_STATE.md` with the run
ID so the claim is checkable.

## Implementation sequence

1. Add a documented, non-behavioral line to `.github/workflows/ci.yml` and `.github/workflows/release.yml`
   and push it. GitHub re-reads the `on:` block of a modified workflow.
2. Immediately push a second, trivial commit and observe whether T1 fires.
3. If T1 does not fire, force fresh registration by adding the workflow under a new filename and
   retiring the old one in the same commit, then repeat step 2.
4. If triggers still do not fire, stop and escalate: capture the repository settings, the workflow
   list, and the API responses, and open a GitHub Support case. Do not paper over it with manual
   dispatch; that is the defect this milestone exists to remove.
5. Once T1 is green, verify T3 with a scratch pull request, then close the milestone and record the
   canonical run ID.

## Failure modes

- **Re-registration does not help.** Fall back to a new filename (step 3), which forces GitHub to
  treat it as a new workflow.
- **Triggers work but CI fails on the touching commit.** Triage normally; the release is blocked
  until `main` is green, which is the point.
- **Windows job flakes.** It is the historical failure mode (P2-M023). A red Windows job is a real
  failure, not a reason to bypass.
- **Org-level restriction is discovered.** Record it, do not work around it; the release pipeline
  cannot function without push triggers.

## Documentation impact

- `PROJECT_STATE.md`: replace the evidence-gap known issue with the resolved facts and the run ID.
- `docs/MILESTONES.md`: closure signal and status.
- `CHANGELOG.md`: note the restored gate under `0.4.0`.
- `README.md`: the CI badge becomes meaningful again; no wording change required.

## Quality gates

- `cargo run -p xtask --locked -- validate`
- `cargo run -p xtask --locked -- validate-plan-policy`
- `./scripts/check-text-files`
- All seven CI jobs green on a pushed commit.

## Acceptance criteria

- [ ] A push to `main` creates an AgentForge CI run for that commit with no manual dispatch.
- [ ] The run is green on all seven jobs.
- [ ] A `pull_request` event creates a run.
- [ ] `workflow_dispatch` still works.
- [ ] The run ID is recorded in `PROJECT_STATE.md`.
- [ ] No check, job, or step was removed to make this pass.

## Completion record

Implementation commit: `d6f11e3` (workflow files), `5d018b2` (registration probe), `d30c73c`
(milestone status vocabulary)
CI run: `40628725474` — push event on `d30c73c`
CI result: success, seven of seven jobs
Completed: 2026-10-27
Notes: Modifying the existing workflow files did **not** re-register their triggers; `updated_at`
never moved and no run appeared. Adding a brand-new workflow file to `.github/workflows/` did:
GitHub re-indexed the whole directory, and `AgentForge CI`, `AgentForge Pages`, and the probe all
fired from the same push. The probe was removed in `d30c73c`, after which pushes still triggered,
so the fix is durable. `pull_request` was verified by opening and immediately closing PR #1
(`event=pull_request` run created for `ae96cee`), and `workflow_dispatch` still runs.

The same push also surfaced a latent defect in the newly live pipeline: `site-updates` accepts only
`planned`, `active`, `complete`, `blocked`, `split`, and `superseded`, so the milestone row had to
use `active` rather than `approved`. That failure is exactly the class of error the gate now
catches, and it was found within minutes of restoring the trigger.
