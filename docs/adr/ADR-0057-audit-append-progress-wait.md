# ADR-0057: Progress-based audit append-lock waiting

- Status: Accepted
- Date: 2026-10-27
- Milestone: P6-M002
- Amends: ADR-0047 (coordinated audit appends)

## Context

ADR-0047 gave every audit append a lock file, taken with `create_new` and held only long enough to
refresh, write one buffer, and fsync. It also fixed a single five-second budget for waiting and
rejected OS advisory locks because `File::lock` was newer than the 1.85 MSRV.

That budget assumes a waiter is blocked by one slow holder. It does not hold when several writers
take turns: the wait is polled every 2 ms by every contender, so they retry in phase, and a writer
can keep losing the race while every other writer is succeeding. The work is progressing the whole
time, yet the losing waiter fails. That is dogfooding finding 23, seen once in CI during P3-M006 in
`concurrent_writers_keep_one_verified_chain` and never reproduced on a local run.

A fixed deadline cannot distinguish "nobody is making progress" from "everybody else is making
progress and I am unlucky". Those two states want opposite responses: the first should fail fast,
the second should keep waiting.

## Decision

- **The wait is tied to observed progress, not to a single deadline.** A waiter samples the lock
  file's fingerprint (existence, length, and modification time). Each change means a different
  holder took the lock in between, so the wait is alive and the stuck timer resets.
- **Two thresholds replace one.** A waiter fails when the fingerprint has not changed for five
  seconds — a crashed or wedged holder, which keeps ADR-0047's operator-facing message — or when
  the total wait exceeds sixty seconds, which keeps the bound finite.
- **Backoff is jittered and grows.** The fixed 2 ms poll becomes a delay starting at 1 ms, doubling
  to a 16 ms cap, with a small xorshift jitter seeded from the process id and the clock. Breaking
  the lockstep is the part that improves fairness; the growing interval removes needless polling.
- **Nothing about ownership changes.** The lock is still `create_new`, still removed only by its
  holder, still never stolen, and the critical section is unchanged. A stale lock is still reported
  with the instruction to remove it once no `forge` or `forged` process is running.
- **No dependencies and no MSRV change.** The fingerprint uses `std::fs::Metadata`, and the jitter
  is a few lines of arithmetic.

## Consequences

Positive:

- a contended writer keeps waiting while other writers are succeeding, so the CI failure mode
  disappears without weakening the stuck-lock diagnostic;
- polling cost drops under load because the backoff grows;
- a genuinely stuck lock is still reported within five seconds, not sixty.

Negative:

- the worst-case wait rises from five to sixty seconds, and only in the "others are working" case;
- a filesystem whose modification time is coarse could report fewer progress events than actually
  occurred, which delays the reset but cannot cause a false failure, because each acquisition
  rewrites a fresh `pid=` line;
- this is not a strict FIFO queue, so a waiter can still in principle wait longer than a newcomer.

## Alternatives considered

- **OS advisory locks (`File::lock`).** Still outside the 1.85 MSRV, and still rejected in
  ADR-0047.
- **A FIFO ticket queue built from files.** It gives a real fairness guarantee, but a crashed
  waiter leaves a ticket that blocks every successor, and stealing another writer's ticket is the
  one behavior the audit log must never normalize. The failure mode is worse than unfairness, in a
  component whose whole purpose is integrity.
- **Reject stale appends and retry at every call site.** Touches every caller, and still allows
  attempt-log batches to interleave.
- **Simply raising the five-second budget.** It hides the same defect at a larger constant and
  makes a genuinely stuck lock slower to report.
