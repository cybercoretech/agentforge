# Doctor and status diagnostics

`forge doctor` and `forge status` are bounded, read-only observations. They report repository
readiness, active plan pointers, project-state presence, and known diagnostic limits in stable line
order. Missing files are findings rather than panics. These commands never repair state, mutate
worktrees, transition tasks, launch agents, or inspect secrets.

## Agent profiles

`forge doctor [<root>] --agents` appends a report on the project's local agent profiles. It is
provider-neutral: it reads `.forge/agents/*.conf` and the filesystem, names no agent, runs no
executable (not even `--version`), opens no network connection, and writes nothing. `<root>`
defaults to the current directory.

Each profile is reported in identifier order:

| Line | Level | Meaning |
| --- | --- | --- |
| `profile <id>` | `ok` | the profile loads within its bounds, and its executable is an absolute existing regular file |
| `profile <id>` | `fail` | the profile does not load; the detail is the loader's cause |
| `profile <id> executable` | `ok`/`fail` | the executable bit is set, where the platform models one |
| `profile <id> argument` | `fail` | an absolute `argument=` path does not exist |
| `profile <id> preflight` | `warn` | the arguments include `--no-preflight`, which disables the preflight of an AgentForge bridge |

A healthy profile produces one `ok` line; only a problem adds a second line. Every non-ok line is
followed by an indented `  fix:` line. The section ends with

```text
agents doctor: N profile(s), F failing check(s), W warning(s)
```

The exit status is 0 when no check fails, 1 when at least one profile must not be used, and 2 for
invalid usage (an unknown option, a repeated `--agents`, or more than one root). An absent or empty
`.forge/agents` directory is healthy and exits 0.

Without `--agents`, `forge doctor` prints exactly the five system lines it printed before and exits
0. `forge doctor <root>` observes that root, as the README already documented.

These checks are static and deliberately stop short of the agent. Whether a specific bridge can run
— its version, flags, sandbox or policy, model catalog, and write roots — is the bridge's own
preflight. Doctor refuses a profile that cannot start; the preflight decides whether the agent it
names can run.
