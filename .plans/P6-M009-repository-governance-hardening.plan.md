# Plan: P6-M009 — Repository governance hardening

Status: Complete
Milestone: P6-M009
Created: 2026-10-31

## Goal

Make the canonical public repository enforce the engineering controls that AgentForge documents:
protected `main`, GitHub security automation, immutable third-party GitHub Actions references, and
bounded Pages and release jobs.

## Scope

- Require pull requests and the documented CI checks for `main`; prevent force pushes and branch
  deletion.
- Enable GitHub secret scanning, push protection, Dependabot security updates, and immutable action
  SHA pinning for the repository.
- Replace every third-party action tag in `.github/workflows/` with the exact commit SHA currently
  published by that action version, keeping the version as an inline comment.
- Add explicit job timeouts to Pages and release workflows.
- Document the repository-control baseline in `docs/CI.md`.

## Non-goals

- No application behavior, dependency, release artifact, or agent-bridge changes.
- No new CI job, cache, hosted runner, or secret.
- No change to the established CI required-check names.
- No speculative dependency upgrades.

## Expected file boundary

- `.plans/P6-M009-repository-governance-hardening.plan.md`, `.plans/ACTIVE`
- `.github/workflows/ci.yml`, `.github/workflows/pages.yml`, `.github/workflows/release.yml`
- `docs/CI.md`
- `PROJECT_STATE.md`, `docs/MILESTONES.md`, `CHANGELOG.md`

GitHub repository settings are an accompanying external boundary:

- `main` branch protection;
- secret scanning and push protection;
- Dependabot security updates;
- Actions SHA-pinning requirement.

## Implementation sequence

1. Commit this approved plan and active-plan pointer without implementation changes.
2. Verify the plan-only checkpoint locally and with exact-head CI.
3. Configure the GitHub repository controls with the GitHub CLI/API.
4. Pin action references and add timeouts.
5. Update CI documentation and completion evidence.
6. Run the full gate, push, confirm exact-head CI and Pages, then close and tag the milestone.

## Quality gates

- `./scripts/check-text-files`
- `cargo run -p xtask --locked -- validate`
- `cargo run -p xtask --locked -- validate-plan-policy`
- `./scripts/gate.sh full`
- Exact-head `AgentForge CI` and `AgentForge Pages` runs

## Acceptance criteria

- [ ] `main` rejects direct pushes, force pushes, and deletion; pull requests require the
      documented checks to pass against the current base.
- [ ] Secret scanning, push protection, and Dependabot security updates are enabled.
- [ ] GitHub Actions requires pinned full-length action SHAs, and every workflow reference is
      pinned to the intended action release.
- [ ] Every Pages and release job has an explicit timeout.
- [ ] CI documentation accurately names the control baseline and timeout policy.
- [ ] The full local gate and exact-head remote CI/Pages runs are green.

## Completion record

Implementation commit: `992dda9c0f1f505e1af3146f9e59fc98984e3656`
CI run: `41263057814` (`AgentForge CI`, exact implementation SHA)
Pages: `41263057670` (`AgentForge Pages`, exact implementation SHA)
Completed: 2026-10-31
Notes: Secret scanning with push protection, Dependabot security updates, and action SHA-pinning
are enabled on the canonical repository. `main` protection is applied after this closure commit's
exact-head validation so it cannot block the governed migration.
