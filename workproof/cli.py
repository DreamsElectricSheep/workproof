"""Command line: `python -m workproof check|record`.

Exit codes: 0 healthy, 1 findings, 2 the checker itself failed. A monitor that
crashes quietly reproduces the bug it exists to catch, so crashes are loud and
distinct from findings.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from . import __version__, core


def _scheduled(args):
    if args.crontab_file:
        with open(args.crontab_file, encoding='utf-8') as f:
            return core.scheduled_from_crontab(f.read())
    if args.crontab:
        out = subprocess.run(['crontab', '-l'], capture_output=True, text=True, check=True).stdout
        return core.scheduled_from_crontab(out)
    return None


def cmd_check(args) -> int:
    registry = core.load_registry(args.registry)
    findings = core.check(registry, args.store, scheduled=_scheduled(args))
    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    elif findings:
        for f in findings:
            print(f'{f.kind.upper():22} {f.agent}: {f.detail}')
    else:
        print(f'OK: {len(registry["agents"])} agents, all producing new work')
    return 1 if findings else 0


def cmd_status(args) -> int:
    rows = core.status(core.load_registry(args.registry), args.store)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    fmt = lambda v: '-' if v is None else f'{v:g}h'  # noqa: E731
    print(f'{"AGENT":28} {"RUNS":>5} {"SINCE RUN":>10} {"SINCE NEW WORK":>15} {"LIMIT":>7}')
    for r in rows:
        print(f'{r["agent"]:28} {r["runs"]:>5} {fmt(r["last_run_h"]):>10} {fmt(r["last_change_h"]):>15} '
              f'{r["limit_h"]:g}h'.rstrip())
    return 0


def cmd_record(args) -> int:
    if args.file:
        raw = core.read_evidence_file(args.file)
    elif args.error:
        raw = b''   # a failed run has no evidence; do not block waiting on stdin
    else:
        raw = sys.stdin.buffer.read()
    core.record(args.agent, raw, args.store, error=args.error)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog='workproof', description=__doc__.splitlines()[0])
    p.add_argument('--version', action='version', version=f'workproof {__version__}')
    sub = p.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('check', help='compare recorded evidence with the registry')
    c.add_argument('--registry', required=True)
    c.add_argument('--store', default='.workproof')
    c.add_argument('--json', action='store_true')
    c.add_argument('--crontab', action='store_true', help='also check coverage against `crontab -l`')
    c.add_argument('--crontab-file', help='also check coverage against a crontab text file')
    c.set_defaults(fn=cmd_check)

    s = sub.add_parser('status', help='show how long each agent has been quiet')
    s.add_argument('--registry', required=True)
    s.add_argument('--store', default='.workproof')
    s.add_argument('--json', action='store_true')
    s.set_defaults(fn=cmd_status)

    r = sub.add_parser('record', help='record one run from the shell (for non-Python agents)')
    r.add_argument('agent')
    r.add_argument('--store', default='.workproof')
    r.add_argument('--file', help='evidence file (tail is hashed); default reads stdin')
    r.add_argument('--error', help='record this run as failed with this message')
    r.set_defaults(fn=cmd_record)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:  # noqa: BLE001
        print(f'workproof: {args.cmd} failed: {type(e).__name__}: {e}', file=sys.stderr)
        return 2
