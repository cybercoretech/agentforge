# Plan: P6-M003 — Release v0.4.0

Status: Approved
Milestone: P6-M003
Created: 2026-10-27

## Goal

Publish a verifiable `v0.4.0` from the canonical repository: four platform archives, their
checksums, a CycloneDX SBOM, a consolidated `SHA256SUMS`, and build-provenance plus SBOM
attestations that `gh attestation verify` accepts against `cybercoretech/agentforge`. Afterwards
the README and the public site must stop saying the repository has no release artifacts.

## Non-goals

- No crates.io publication. `docs/REGISTRY.md` keeps that behind its own plan.
- No change to the artifact set, the target matrix, or the version scheme.
- No moving or deleting a published tag. A defect ships as a new patch release.
- No feature work. `0.4.0` contains external MCP servers (P3-M006), the restored CI gate
  (P6-M001), and the audit append-lock fix (P6-M002).

## Context

`release.yml` has never executed on this repository: it triggers on `push: tags: v*.*.*`, and tag
pushes produced no runs until P6-M001 restored event triggers. The releases API is empty and the
`v0.3.1` tag carries only GitHub's automatic source archives, so the project's central claim —
attested, verifiable releases — has been unverifiable here since the migration.

Two things make this safe to do now. First, the release workflow supports a full rehearsal through
`workflow_dispatch`, which builds every target, generates and attests the SBOM, uploads through the
same `gh release create` into a draft, verifies every asset, and deletes the draft. Second,
`docs/RELEASE.md` already documents the recovery path for a failed publish job without moving the
tag.

## Architecture placement

Version metadata and release documentation only. No crate logic changes.

## Data flow

Version commit → green exact-head CI → annotated `v0.4.0` tag → `release.yml` build matrix (four
targets) → SBOM generation and merge → checksums → keyless attestations → `publish-release tag` →
asset download-back verification.

## Invariants

- The tag version equals the workspace version exactly.
- Four archives, four `.sha256` files, one SBOM, one `SHA256SUMS`: ten assets.
- Every archive carries both attestation types, verifiable against `cybercoretech/agentforge`.
- The release is cut from the commit that passed CI, never from a later or earlier one.

## ADRs

- ADR-0053 — keyless build provenance.
- ADR-0054 — attested CycloneDX SBOM.
- ADR-0044 — milestone tags are separate from release tags.
- ADR-0056 — evidence before features, Phase 0.

## Public API / CLI

No API change. `forge version` and `forged version` must report `0.4.0`.

## Compatibility analysis

`0.4.x` is a minor release on an alpha line. Workspace path dependencies are pinned to the
workspace version, so `crates/agentforge-cli/Cargo.toml` needs its thirteen `version = "0.3.1"`
entries moved to `0.4.0` in the same commit, or Cargo will refuse the tree.

## Dependency analysis

No new dependencies. The publish job installs `cargo-cyclonedx` 0.5.9 as it always has; the
rehearsal exercises that install.

## Expected file boundary

- `.plans/P6-M003-release-v0-4-0.plan.md`, `.plans/ACTIVE`
- `Cargo.toml`, `Cargo.lock`, `crates/agentforge-cli/Cargo.toml`
- `CHANGELOG.md`, `README.md`, `PROJECT_STATE.md`, `docs/RELEASE.md`, `docs/MILESTONES.md`
- `site/index.html` (version pill and status line)

## Test-first matrix

| Check | Method | Expected |
| --- | --- | --- |
| T1 rehearsal | `gh workflow run release.yml --ref main`, then inspect the run | build, SBOM, attestation, draft upload, and verification all succeed; the draft is deleted and no tag is created |
| T2 version consistency | `cargo metadata` and the tag | workspace version equals the tag body |
| T3 full gate | `./scripts/gate.sh full` | clean |
| T4 exact-head CI | push-triggered run on the version commit | seven of seven green |
| T5 tag run | push `v0.4.0` | release created from that commit |
| T6 asset set | releases API | exactly ten assets |
| T7 provenance | `gh attestation verify <archive> -R cybercoretech/agentforge --signer-workflow cybercoretech/agentforge/.github/workflows/release.yml` | passes for all four archives |
| T8 SBOM attestation | same with `--predicate-type https://cyclonedx.org/bom` | passes for all four archives |
| T9 binary version | download the Linux archive, extract, run `./forge version` | `0.4.0` |

## Implementation sequence

1. Dispatch the release rehearsal and fix anything it exposes before touching the version.
2. Bump the workspace version and the CLI path-dependency versions; regenerate `Cargo.lock`.
3. Move `CHANGELOG.md`'s `[Unreleased]` to `[0.4.0] - <date>`, and correct every document that
   names the current release or the artifact gap.
4. Run `./scripts/gate.sh full`, commit, push, and wait for exact-head CI.
5. Create the annotated tag and push it; watch the release run to completion.
6. Verify T6–T9 from this machine, record the evidence, and close the milestone.

## Failure modes

- **The rehearsal fails at SBOM generation.** Fix forward before the version bump; nothing is
  published by a rehearsal.
- **The tag run fails before uploading.** Re-run the failed job; do not move the tag.
- **The tag run uploads but verification fails.** Follow `docs/RELEASE.md`: compare the release
  assets with the run's artifacts and `gh release upload --clobber` from those artifacts.
- **The tag run fails at publish.** Publish from the same run's artifacts as documented, accept
  that such a release has no attestations, and fix the workflow for the next release.
- **Attestation service outage.** Nothing was uploaded; re-run the job.

## Documentation impact

- `README.md`: current release becomes `0.4.0` with real artifact links; the migrated-tag note
  stays for `v0.3.1` as history.
- `site/index.html`: version pill and status line.
- `docs/RELEASE.md`: current-line paragraph.
- `PROJECT_STATE.md`, `docs/MILESTONES.md`, `CHANGELOG.md`: release evidence.

## Quality gates

- `./scripts/gate.sh full`
- Seven of seven CI jobs green on the exact tagged commit
- `gh attestation verify` for both predicate types on all four archives
- `gh release view v0.4.0` lists ten assets

## Acceptance criteria

- [ ] A dispatched rehearsal completes and leaves no draft and no tag.
- [ ] `v0.4.0` is published from a commit whose CI is green.
- [ ] The release has ten assets.
- [ ] Both attestation types verify for all four archives against the canonical repository.
- [ ] The extracted Linux `forge` reports `0.4.0`.
- [ ] README and the site no longer claim the repository has no release artifacts.
- [ ] No tag was moved or deleted.

## Completion record

Implementation commit:
CI run:
Release run:
Completed:
Notes:
