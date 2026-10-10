"""Proof-of-work monitoring for scheduled agents. Standard library only.

An agent records evidence of what it produced after every run. A separate
checker (cron, CI, anything) compares the evidence against a registry and
reports agents that stopped running, kept running without producing anything
new, or produce empty output.

What this catches: agents that are dead or idle while looking healthy.
What it does NOT catch: agents that produce changing but wrong output.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

TAIL_BYTES = 65536  # hash only the tail of large evidence files
EMPTY_FORMS = (b'', b'null', b'[]', b'{}', b'""')
_SLUG = re.compile(r'[^A-Za-z0-9_.-]+')


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── evidence ────────────────────────────────────────────────────────────────

def to_evidence(obj) -> bytes:
    """Canonical bytes for whatever an agent returned, so equal output hashes equal."""
    if obj is None:
        return b''
    if isinstance(obj, bytes):
        return obj
    if isinstance(obj, str):
        return obj.encode('utf-8')
    try:
        return json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8')
    except (TypeError, ValueError):
        return repr(obj).encode('utf-8')


def is_empty(raw: bytes) -> bool:
    return raw.strip() in EMPTY_FORMS


def read_evidence_file(path) -> bytes:
    """Tail of a file (large logs only need their newest bytes). b'' if missing."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
            return f.read()
    except OSError:
        return b''


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


# ── store ───────────────────────────────────────────────────────────────────

def _path(store, agent: str) -> Path:
    return Path(store) / (_SLUG.sub('_', agent).strip('_') + '.json')


def load_record(store, agent: str):
    p = _path(store, agent)
    try:
        rec = json.loads(p.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if rec.get('agent') != agent:
        raise ValueError(f'store name collision: {p.name} belongs to {rec.get("agent")!r}, not {agent!r}')
    return rec


def _write_atomic(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record(agent: str, evidence=b'', store='.workproof', *, error=None, now=None) -> dict:
    """Record one run. Call at the end of every run, success or failure.

    Evidence counts as new work only if it is non-empty AND differs from the last
    non-empty evidence. A failed run (error set) updates last_run but never counts
    as work, and empty output never counts as work.
    """
    raw = evidence if isinstance(evidence, bytes) else to_evidence(evidence)
    now = now or _utcnow()
    iso = now.isoformat()
    prev = load_record(store, agent) or {}
    h, empty = digest(raw), is_empty(raw)

    if error is not None:
        rec = {**prev, 'agent': agent, 'last_run': iso, 'last_error': str(error)[:500],
               'consecutive_errors': prev.get('consecutive_errors', 0) + 1,
               'runs': prev.get('runs', 0) + 1}
        rec.setdefault('hash', h)
        rec.setdefault('last_change', iso)
        rec.setdefault('empty', empty)
    else:
        changed = (not empty) and h != prev.get('hash')
        rec = {'agent': agent, 'hash': h if not empty else prev.get('hash', h), 'empty': empty,
               'last_run': iso, 'runs': prev.get('runs', 0) + 1,
               'last_change': iso if changed or 'last_change' not in prev and not empty
               else prev.get('last_change', iso),
               'last_error': None, 'consecutive_errors': 0}
    _write_atomic(_path(store, agent), rec)
    return rec


def _safe_record(*args, **kwargs) -> None:
    """A monitor must never break the thing it monitors: report and carry on.
    The checker will flag the agent as not running if recording keeps failing."""
    try:
        record(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        print(f'workproof: could not record heartbeat: {type(e).__name__}: {e}', file=sys.stderr)


def proves_work(agent: str, *, store='.workproof', evidence=None, evidence_file=None):
    """Decorator: record the return value (or evidence(result), or a file's tail)
    after each run. Exceptions are recorded, then re-raised."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                result = fn(*a, **k)
            except Exception as e:
                _safe_record(agent, b'', store, error=f'{type(e).__name__}: {e}')
                raise
            if evidence_file is not None:
                raw = read_evidence_file(evidence_file)
            elif evidence is not None:
                raw = to_evidence(evidence(result))
            else:
                raw = to_evidence(result)
            _safe_record(agent, raw, store)
            return result
        return wrapper
    return deco


class watch:
    """Context manager form: `with watch('name') as w: ...; w.evidence = output`."""

    def __init__(self, agent: str, store='.workproof'):
        self.agent, self.store, self.evidence = agent, store, None

    def __enter__(self):
        return self

    def __exit__(self, etype, exc, tb):
        if etype is not None and issubclass(etype, Exception):
            _safe_record(self.agent, b'', self.store, error=f'{etype.__name__}: {exc}')
        else:
            _safe_record(self.agent, to_evidence(self.evidence), self.store)
        return False


# ── checking ────────────────────────────────────────────────────────────────

def weekday_hours(start: datetime, end: datetime) -> float:
    """Hours between start and end counting Mon-Fri only (UTC).

    A weekday-only agent idle over a weekend has missed nothing, but wall-clock age
    says 48h+. Without this, every Monday raised false alarms for healthy agents.
    """
    if end <= start:
        return 0.0
    total, cur = 0.0, start
    while cur < end:
        midnight = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        nxt = min(end, midnight)
        if cur.weekday() < 5:
            total += (nxt - cur).total_seconds() / 3600
        cur = nxt
    return total


def _age_hours(since_iso: str, now: datetime, weekdays_only: bool) -> float:
    since = datetime.fromisoformat(since_iso)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if weekdays_only:
        return weekday_hours(since, now)
    return max(0.0, (now - since).total_seconds() / 3600)


@dataclass
class Finding:
    kind: str    # never_seen | no_run | no_new_work | empty_output | last_run_failed
    agent: str   # | unmonitored | retired_but_scheduled
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


def load_registry(path) -> dict:
    with open(path, encoding='utf-8') as f:
        reg = json.load(f)
    agents = reg.get('agents')
    if not isinstance(agents, dict) or not agents:
        raise ValueError('registry needs a non-empty "agents" object')
    for name, cfg in agents.items():
        gap = cfg.get('max_gap_hours')
        if not isinstance(gap, (int, float)) or gap <= 0:
            raise ValueError(f'agent {name!r}: max_gap_hours must be a positive number')
    return reg


def scheduled_from_crontab(text: str) -> set:
    """Script names in the ACTIVE (uncommented) lines of a crontab."""
    found = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        found.update(re.findall(r'([A-Za-z0-9_\-]+\.(?:py|sh|js))', s))
    return found


def check(registry: dict, store='.workproof', *, scheduled=None, now=None) -> list:
    """Compare recorded evidence with the registry. Returns a list of Finding."""
    now = now or _utcnow()
    findings = []
    agents = registry['agents']
    silent = registry.get('no_evidence_expected', {})
    retired = registry.get('retired', {})

    for name, cfg in agents.items():
        wk = bool(cfg.get('weekdays_only'))
        gap = float(cfg['max_gap_hours'])
        run_gap = float(cfg.get('max_run_gap_hours', gap))
        rec = load_record(store, name)
        if rec is None:
            findings.append(Finding('never_seen', name, 'declared in the registry but has never recorded a run'))
            continue

        run_age = _age_hours(rec['last_run'], now, wk)
        if run_age > run_gap:
            findings.append(Finding('no_run', name, f'has not run in {run_age:.0f}h (limit {run_gap:g}h)'))
            continue

        if rec.get('consecutive_errors', 0) > 0:
            findings.append(Finding('last_run_failed', name,
                                    f'{rec["consecutive_errors"]} consecutive failed run(s): {rec.get("last_error")}'))

        if rec.get('empty'):
            findings.append(Finding('empty_output', name, 'last run produced empty output'))
            continue

        change_age = _age_hours(rec['last_change'], now, wk)
        if change_age > gap:
            findings.append(Finding('no_new_work', name,
                                    f'running, but output unchanged for {change_age:.0f}h (limit {gap:g}h)'))

    if scheduled is not None:
        declared = {cfg.get('script') for cfg in agents.values() if cfg.get('script')}
        declared |= {k.split()[0] for k in silent} | set(retired)
        for script in sorted(set(scheduled) - declared):
            findings.append(Finding('unmonitored', script, 'scheduled but not declared in the registry'))
        for script in sorted(set(retired) & set(scheduled)):
            findings.append(Finding('retired_but_scheduled', script,
                                    f'marked retired ({retired[script]}) but still scheduled'))
    return findings


def status(registry: dict, store='.workproof', *, now=None) -> list:
    """One row per declared agent: how long since it last ran and last produced new work."""
    now = now or _utcnow()
    rows = []
    for name, cfg in registry['agents'].items():
        wk = bool(cfg.get('weekdays_only'))
        rec = load_record(store, name)
        row = {'agent': name, 'limit_h': cfg['max_gap_hours'], 'runs': 0, 'last_run_h': None, 'last_change_h': None}
        if rec is not None:
            row.update(runs=rec.get('runs', 0),
                       last_run_h=round(_age_hours(rec['last_run'], now, wk), 1),
                       last_change_h=round(_age_hours(rec['last_change'], now, wk), 1))
        rows.append(row)
    return rows
