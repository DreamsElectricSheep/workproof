# workproof

Proof-of-work monitoring for scheduled and AI agents. Standard library only, Python 3.9+.

Normal health checks verify an **artifact**: the process is up, the file exists, the file is recent. An agent can pass every one of those while doing nothing. It crashes and rewrites an empty file. It swallows a fatal error and exits 0. Its cron entry was never added. workproof checks that **new work was produced**.

## Why this exists

It was extracted from a monitor written after an audit of a fleet of scheduled agents found four of them dead for between 5 days and 6 weeks while every existing check reported healthy:

- one had not run for **six weeks** and was in no monitor at all;
- one had been dead nine days, **rewriting a fresh but empty file** every day;
- one had been gutted by an automated code edit, still compiled and imported, and did nothing for **three weeks**;
- one hit a fatal error and **exited 0**, so the restart policy never fired.

Every check verified an artifact. None verified that work happened.

## How it works

Two parts, deliberately separate:

1. **The agent records evidence** after every run (decorator, context manager, or a shell command).
2. **A checker you run from cron or CI** compares the evidence with a registry and exits non-zero on findings. Because the checker is separate, it also catches an agent that never starts, which a decorator alone cannot see.

Evidence is tracked by **content hash, not file age**. Output identical to the last run does not count as new work, and empty output never does.

## Quick start

```bash
pip install workproof
```

Record evidence from Python:

```python
from workproof import proves_work

@proves_work("daily-summarizer", store=".workproof")
def run():
    ...
    return summary          # hashed; the same output twice is not new work
```

Or as a context manager, or from the shell for agents in any language:

```python
from workproof import watch

with watch("nightly-sync") as w:
    rows = sync()
    w.evidence = rows
```

```bash
./my_agent.sh > out.json && workproof record my-agent --file out.json
./my_agent.sh || workproof record my-agent --error "exit code $?"
```

Declare what you expect in a registry:

```json
{
  "agents": {
    "daily-summarizer": {"script": "summarize.py", "max_gap_hours": 30, "weekdays_only": true},
    "nightly-sync":     {"script": "sync.py",      "max_gap_hours": 30}
  },
  "no_evidence_expected": {"cleanup.py": "legitimately produces nothing"},
  "retired": {"old_bot.py": "replaced by daily-summarizer"}
}
```

Run the checker from cron or CI:

```bash
workproof check --registry agents.json --store .workproof --crontab
workproof status --registry agents.json --store .workproof
```

Exit code `0` healthy, `1` findings, `2` the checker itself failed. A monitor that crashes quietly reproduces the bug it exists to catch, so a crash is loud and distinct from a finding.

## What it reports

| Finding | Meaning |
|---|---|
| `never_seen` | declared in the registry but has never recorded a run |
| `no_run` | has not run within its limit (the job is not firing) |
| `no_new_work` | it runs, but its output has been identical for too long |
| `empty_output` | the last run produced nothing |
| `last_run_failed` | consecutive failed runs (a failed run never counts as work) |
| `unmonitored` | scheduled in cron but not declared in the registry |
| `retired_but_scheduled` | marked retired but still scheduled |

Registry options per agent: `max_gap_hours` (required), `max_run_gap_hours`, `weekdays_only`, `script`. Weekday-only agents are measured in weekday hours, so a quiet weekend does not raise a false alarm every Monday.

## What it does not do

- It detects **dead or idle** agents, not **wrong** ones. An agent emitting changing garbage passes.
- Output that is legitimately constant will be flagged. Declare it under `no_evidence_expected` or give it a longer limit.
- Weekdays are evaluated in UTC.
- One writer per agent name is assumed (no file locking).
- There is no dashboard and no built-in alerting. Wire the exit code or `--json` output into whatever you already use.

## Is a hosted version useful?

I am deciding whether to build a hosted version with alerting, a dashboard and multi-agent history. If your team would use it, or if something here does not fit how you run agents, please [open an issue](https://github.com/DreamsElectricSheep/workproof/issues) and describe your setup. That is the most useful thing you can send.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite reproduces the four incidents above.

## License

MIT
