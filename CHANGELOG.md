# Changelog

## 0.1.0

First release. Extracted from a monitor that ran a fleet of scheduled agents for several months.

- Evidence recording: decorator, context manager, and a shell command for non-Python agents.
- Checker CLI with distinct exit codes: 0 healthy, 1 findings, 2 the checker itself failed.
- Findings: `never_seen`, `no_run`, `no_new_work`, `empty_output`, `last_run_failed`, `unmonitored`, `retired_but_scheduled`.
- Weekday-aware limits (UTC), optional crontab coverage check, `status` command.
- Standard library only, Python 3.9+.
