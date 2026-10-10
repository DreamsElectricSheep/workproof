import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workproof import (check, proves_work, record, scheduled_from_crontab,  # noqa: E402
                       status, watch, weekday_hours)

T0 = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)  # a Monday
H = timedelta(hours=1)
D = timedelta(days=1)


def kinds(findings):
    return sorted((f.kind, f.agent) for f in findings)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def reg(self, **agent_cfg):
        cfg = {'max_gap_hours': 30}
        cfg.update(agent_cfg)
        return {'agents': {'a': cfg}}


class BasicBehaviour(Base):
    def test_changing_output_is_healthy(self):
        for i in range(5):
            record('a', f'result {i}', self.store, now=T0 + i * D)
        self.assertEqual(check(self.reg(), self.store, now=T0 + 4 * D + 2 * H), [])

    def test_declared_but_never_recorded(self):
        self.assertEqual(kinds(check(self.reg(), self.store, now=T0)), [('never_seen', 'a')])

    def test_failed_run_never_counts_as_work(self):
        record('a', 'good', self.store, now=T0)
        record('a', b'', self.store, error='boom', now=T0 + 20 * H)
        f = check(self.reg(), self.store, now=T0 + 40 * H)
        self.assertIn(('last_run_failed', 'a'), kinds(f))
        self.assertIn(('no_new_work', 'a'), kinds(f))


class RealIncidents(Base):
    """The four failures that motivated this tool, reproduced."""

    def test_eqls_dead_six_weeks_never_runs(self):
        record('a', 'last good run', self.store, now=T0)
        f = check(self.reg(max_gap_hours=200), self.store, now=T0 + 42 * D)
        self.assertEqual(kinds(f), [('no_run', 'a')])

    def test_x_scanner_rewrites_empty_file_daily(self):
        record('a', 'real data', self.store, now=T0)
        for i in range(1, 10):
            record('a', '[]', self.store, now=T0 + i * D)
        f = check(self.reg(), self.store, now=T0 + 9 * D + H)
        self.assertEqual(kinds(f), [('empty_output', 'a')])

    def test_13f_gutted_runs_daily_but_output_frozen(self):
        for i in range(21):
            record('a', 'identical constant output', self.store, now=T0 + i * D)
        f = check(self.reg(), self.store, now=T0 + 20 * D + H)
        self.assertEqual(kinds(f), [('no_new_work', 'a')])

    def test_allora_fatal_error_exits_zero_frozen_log(self):
        # swallowed its own fatal error: no exception reaches the wrapper, log tail frozen
        for i in range(5):
            record('a', 'FATAL: connection refused', self.store, now=T0 + i * D)
        f = check(self.reg(), self.store, now=T0 + 4 * D + H)
        self.assertEqual(kinds(f), [('no_new_work', 'a')])


class Schedules(Base):
    def test_weekday_agent_idle_over_weekend_is_fine(self):
        fri = datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(fri.weekday(), 4)
        record('a', 'friday', self.store, now=fri)
        mon = fri + timedelta(days=3, hours=-7)  # Monday 09:00
        self.assertEqual(check(self.reg(weekdays_only=True), self.store, now=mon), [])
        self.assertEqual(kinds(check(self.reg(), self.store, now=mon)), [('no_run', 'a')])

    def test_weekday_hours_skips_weekend(self):
        fri = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
        mon = datetime(2026, 10, 12, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(weekday_hours(fri, mon), 8.0)


class Coverage(Base):
    def test_unmonitored_and_retired_scheduled(self):
        tab = '# c.py is commented\n0 8 * * 1-5 python /x/a.py\n5 * * * * /x/new_bot.py\n1 1 * * * /x/old.py\n'
        sched = scheduled_from_crontab(tab)
        self.assertEqual(sched, {'a.py', 'new_bot.py', 'old.py'})
        reg = {'agents': {'a': {'max_gap_hours': 30, 'script': 'a.py'}},
               'retired': {'old.py': 'no alpha'}, 'no_evidence_expected': {'silent.py --monitor': 'ok'}}
        record('a', 'x', self.store, now=T0)
        f = check(reg, self.store, scheduled=sched, now=T0 + H)
        self.assertEqual(kinds(f), [('retired_but_scheduled', 'old.py'), ('unmonitored', 'new_bot.py')])


class Wrappers(Base):
    def test_decorator_records_return_value_and_reraises(self):
        @proves_work('a', store=self.store)
        def ok():
            return {'n': 1}

        @proves_work('b', store=self.store)
        def bad():
            raise RuntimeError('nope')

        ok()
        with self.assertRaises(RuntimeError):
            bad()
        reg = {'agents': {'a': {'max_gap_hours': 30}, 'b': {'max_gap_hours': 30}}}
        self.assertEqual(kinds(check(reg, self.store)), [('empty_output', 'b'), ('last_run_failed', 'b')])

    def test_context_manager(self):
        with watch('a', self.store) as w:
            w.evidence = ['row']
        self.assertEqual(check(self.reg(), self.store), [])

    def test_recording_failure_never_breaks_the_agent(self):
        blocker = Path(self.store) / 'file'
        blocker.write_text('x')

        @proves_work('a', store=str(blocker / 'sub'))
        def ok():
            return 'fine'

        self.assertEqual(ok(), 'fine')  # store path is unusable; agent still returns


class Status(Base):
    def test_rows(self):
        record('a', 'x', self.store, now=T0)
        row = status(self.reg(), self.store, now=T0 + 5 * H)[0]
        self.assertEqual((row['runs'], row['last_run_h'], row['last_change_h']), (1, 5.0, 5.0))

    def test_never_seen_agent_has_no_ages(self):
        row = status({'agents': {'z': {'max_gap_hours': 5}}}, self.store)[0]
        self.assertEqual((row['runs'], row['last_run_h']), (0, None))


class Cli(Base):
    def run_cli(self, *args):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent), PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, '-m', 'workproof', *args], capture_output=True, text=True, env=env)

    def test_version_and_status_commands(self):
        regfile = Path(self.store) / 'reg.json'
        regfile.write_text(json.dumps(self.reg()))
        store = str(Path(self.store) / 'hb')
        self.run_cli('record', 'a', '--store', store, '--file', str(regfile))
        v = self.run_cli('--version')
        self.assertEqual((v.returncode, v.stdout.startswith('workproof ')), (0, True))
        s = self.run_cli('status', '--registry', str(regfile), '--store', store)
        self.assertEqual(s.returncode, 0)
        self.assertIn('AGENT', s.stdout)
        self.assertIn('a ', s.stdout)

    def test_record_error_does_not_wait_for_stdin(self):
        regfile = Path(self.store) / 'reg.json'
        regfile.write_text(json.dumps(self.reg()))
        store = str(Path(self.store) / 'hb')
        r = self.run_cli('record', 'a', '--store', store, '--error', 'exit code 3')
        self.assertEqual(r.returncode, 0)
        c = self.run_cli('check', '--registry', str(regfile), '--store', store)
        self.assertIn('LAST_RUN_FAILED', c.stdout)

    def test_exit_codes(self):
        regfile = Path(self.store) / 'reg.json'
        regfile.write_text(json.dumps(self.reg()))
        store = str(Path(self.store) / 'hb')
        self.assertEqual(self.run_cli('check', '--registry', str(regfile), '--store', store).returncode, 1)  # never_seen
        self.assertEqual(self.run_cli('record', 'a', '--store', store, '--file', str(regfile)).returncode, 0)
        r = self.run_cli('check', '--registry', str(regfile), '--store', store)
        self.assertEqual((r.returncode, r.stdout.startswith('OK')), (0, True))
        regfile.write_text('{"agents": {}}')
        r = self.run_cli('check', '--registry', str(regfile), '--store', store)
        self.assertEqual(r.returncode, 2)
        self.assertIn('failed', r.stderr)


if __name__ == '__main__':
    unittest.main()
