# Five ways my scheduled agents failed while every health check was green

I ran about three dozen scheduled bots on one small machine for seven months. They were a mix of research jobs and experiments, driven by cron or systemd, and some of them called LLMs. None of the experiments produced anything I kept, but the failures taught me a lot. In July an audit found that four of the bots had been dead for anywhere from five days to six weeks, and every check I had set up said they were fine.

Those checks tested whether a file existed, whether it was recent, whether the service was active, and whether the module imported. None of them tested whether the bot had done any work.

## 1. A weekly job missing from every monitor

A weekly job stopped running and I did not notice for six weeks. It was not on any monitor's list, so there was nothing to alert.

## 2. An empty file rewritten every day

A scraper broke and then kept "succeeding". Each day it wrote a new, empty output file. The file existed, it was recent, and the service was up. This went on for nine days. A check on file age passes that indefinitely. A check on file content catches it.

## 3. An edit that compiled and did nothing

An LLM-generated edit removed most of the logic from a production script. The script still compiled and still imported, so both of my automated safety checks passed. It did nothing for three weeks. Those checks only show that the code is valid. They do not show that it still does its job.

## 4. A fatal error that exited with code 0

A worker hit a fatal error and exited with status 0. The restart policy only triggers on failure, so it never restarted the worker. For five days the service reported that it had exited cleanly.

## 5. The monitor had the same problem

The monitor I wrote after the audit hashes an evidence file and alerts when the hash stops changing. About three weeks later I found two jobs whose evidence files had been truncated to zero bytes by logrotate. An empty file always has the same hash, so the monitor could never see those jobs fail. One of them was the job whose six-week outage had led me to build the monitor in the first place. Empty evidence has to count as a failure, not as "no change".

## Two smaller problems

- On one Monday, five healthy weekday-only jobs all raised alerts, because the weekend looked like 48 hours without activity. Limits for weekday jobs need to be measured in weekday hours.
- While testing the monitor I triggered about ten real alerts in nine minutes. Two were false alarms caused by test data I had deliberately backdated. A monitor needs a dry-run mode, or its tests look the same as real incidents.

## What I changed

- Evidence is tracked by content hash, not file age. Output identical to the last run does not count as new work, and empty output never does.
- The agent records what it produced. A separate checker, run from cron or CI, compares that with a registry. A decorator alone cannot notice a job that never starts.
- Limits are schedule-aware, the checker flags anything in cron that is not declared, and a crash in the checker itself is reported differently from a finding.

I turned this into a small library called workproof. It uses only the Python standard library.

```python
from workproof import proves_work

@proves_work("daily-summarizer")
def run():
    ...
    return summary
```

```
workproof check --registry agents.json --crontab
```

## What it does not do

It catches agents that are dead or idle. It does not catch agents that are wrong. An agent that produces changing but incorrect output will pass. For output quality you need evaluation tooling in addition to this.

## If you run scheduled or LLM agents

I am deciding whether a hosted version, with alerting and history across many agents, is worth building, or whether a small library is the right size. If you run agents on a schedule, I would like to know how you find out today when one goes quiet. Please open an issue on the repo and describe your setup.

Repo: https://github.com/DreamsElectricSheep/workproof
