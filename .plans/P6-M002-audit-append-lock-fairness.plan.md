# Plan: P6-M002 — Audit append lock fairness

Status: Complete
Milestone: P6-M002
Created: 2026-10-27

## Goal

Remove dogfooding finding 23: the audit append lock can starve a writer past its fixed five-second
budget under contention, even though every contender is making progress. A waiter must keep waiting
while the lock is demonstrably changing hands, and must still fail fast and clearly when the lock is
genuinely stuck.

## Non-goals

- No change to the record format, the chain, the audit schema, or the renumbering policy.
- No change to the "never stolen" rule: a lock is removed only by its holder or by an operator.
- No new dependencies. The crate stays standard-library-only (ADR-0047).
- No MSRV bump; Rust 1.85 remains the floor.
- No OS advisory locks. `File::lock` is still newer than the MSRV (ADR-0047).
- No strict FIFO ticket queue. See the alternatives in ADR-0057.

## Context

`FileAuditStore::append` and `append_batch` take `<log>.lock` with `create_new`, poll every 2 ms,
and give up after a single fixed `APPEND_LOCK_WAIT` of five seconds. The wait is not fair: eight
millisecond-scale contenders can keep re-taking the lock in lockstep while one of them never wins a
race. That happened once in CI (dogfooding finding 23, during P3-M006) and passed on repetition,
which is the signature of a timing defect rather than a logic defect.

The lock is also polled with a constant 2 ms interval for every waiter, so contenders retry in
phase with each other, which makes the unfairness worse.

## Architecture placement

`agentforge-audit` only. `AppendLock` is private to the crate. No public API changes.

## Data flow

Writer wants to append → attempt `create_new(<log>.lock)` → on contention, observe the lock file's
fingerprint → if it changed, a different writer held it in between, so the wait is alive → sleep a
jittered, growing interval → retry. Fail only when the lock's fingerprint has not changed for the
stuck threshold, or when the total budget is exhausted.

## Invariants

- A lock is never removed by a non-holder.
- The critical section is still: acquire, refresh, write one buffer, fsync, release.
- Readers remain lock-free.
- A crashed holder leaves a stale lock that is reported, not silently stolen.
- The bound on total waiting remains finite.

## ADRs

- ADR-0057 — progress-based append-lock waiting (amends ADR-0047).
- ADR-0047 — coordinated audit appends.
- ADR-0014 — append-only audit log with integrity chaining.

## Public API / CLI

None. Error kinds are unchanged; the stuck-lock message keeps its operator guidance.

## Compatibility analysis

Behavioral only, and only under contention. Single-writer projects see identical behavior. A writer
that previously failed after five seconds of contention may now wait longer and succeed; nothing
that previously succeeded can start failing because the change only extends waiting and adds jitter.

## Dependency analysis

No new crates. Jitter is a small xorshift seeded from the process id, a monotonic counter, and the
current time, so no `rand` dependency is added.

## Expected file boundary

- `.plans/P6-M002-audit-append-lock-fairness.plan.md`, `.plans/ACTIVE`
- `docs/adr/ADR-0057-audit-append-progress-wait.md`, `docs/adr/README.md`
- `crates/agentforge-audit/src/lib.rs`
- `crates/agentforge-audit/tests/concurrent_appends.rs`
- `docs/AUDIT.md`, `docs/DOGFOODING.md`, `docs/MILESTONES.md`
- `PROJECT_STATE.md`, `CHANGELOG.md`

## Test-first matrix

| Check | Test | Expected |
| --- | --- | --- |
| T1 progress extends the wait | unit test on the wait predicate with an unchanged interval below the stuck threshold and elapsed time beyond the old five seconds | continue waiting |
| T2 a stuck lock still fails | unit test with an unchanged fingerprint past the stuck threshold | give up |
| T3 the total budget is finite | unit test with recent progress but elapsed past the total budget | give up |
| T4 fingerprint changes on handoff | unit test creating, removing, and recreating the lock file | fingerprint differs |
| T5 no regression under real contention | existing `concurrent_writers_keep_one_verified_chain`, repeated 20× locally | 100 records, one verified chain, no failure |
| T6 jitter is bounded | unit test over many seeds | every delay falls inside the configured range |

## Implementation sequence

1. Extract the wait decision into a pure function over `(elapsed, unchanged_for, thresholds)` and
   unit-test T1–T3 first.
2. Add `lock_fingerprint` (length, modified time, and existence) with T4.
3. Add bounded jittered backoff with T6.
4. Wire both into `AppendLock::acquire`, keeping `create_new`, the Windows delete-pending handling,
   the "never stolen" policy, and the operator-facing message.
5. Update `docs/AUDIT.md` and close finding 23 in `docs/DOGFOODING.md`.

## Failure modes

- **Fingerprint resolution is too coarse on a given filesystem.** Length plus modification time is
  enough because each acquisition rewrites a fresh `pid=` line; a coarse clock still changes at
  least once per second, well inside the stuck threshold.
- **A waiter now waits longer than an operator expects.** The total budget is explicit and the
  stuck case still reports within five seconds, so the slow path only appears when work is actually
  happening.
- **A test becomes timing-dependent.** The decision logic is tested as a pure function; only the
  existing contention test exercises real timing, and it is repeated rather than asserted on a
  duration.

## Documentation impact

- `docs/AUDIT.md`: describe the progress-based wait and its two thresholds.
- `docs/DOGFOODING.md`: mark finding 23 resolved with the cause.
- `docs/MILESTONES.md`, `PROJECT_STATE.md`, `CHANGELOG.md`: closure evidence.

## Quality gates

- `cargo test -p agentforge-audit`
- `cargo test --workspace --locked`
- `cargo clippy --workspace --all-targets --locked -- -D warnings`
- `cargo fmt --all --check`
- `cargo run -p xtask --locked -- validate` and `validate-plan-policy`
- `./scripts/check-text-files`
- All seven CI jobs green on a pushed commit.

## Acceptance criteria

- [ ] A waiter that observes the lock changing hands does not fail while progress continues.
- [ ] A lock whose fingerprint is unchanged for five seconds still fails with the operator message.
- [ ] Total waiting is bounded.
- [ ] A crashed holder's lock is still reported, never stolen.
- [ ] The contention test passes on 20 consecutive local runs.
- [ ] No new dependency and no MSRV change.

## Completion record

Implementation commit: `d928d96`
CI run: `40630130098` — push event on `d928d96`
CI result: success, seven of seven jobs (the first Linux job failed on a `rustup` download timeout,
not on the change, and passed on re-run)
Completed: 2026-10-27
Notes: The wait predicate is split out as a pure function and covered by six unit tests, so the
policy is testable without sleeping. The regression test `a_wedged_lock_is_reported_and_never_stolen`
takes the full five seconds on purpose and asserts that the waiter does not remove the lock.
`concurrent_writers_keep_one_verified_chain` passed 20 consecutive local runs, and the workspace
suite went from 333 to 349 tests.
