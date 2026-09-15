"""Predicate hygiene: move legacy claims onto the predicates they always meant.

Read the report, then decide:

    python scripts/migrate_predicates.py            # report only (default)
    python scripts/migrate_predicates.py --apply    # execute the plan

**Why this is not a DELETE.** A claim's subject/object, its evidence quote, its
relations and its audit row are the reason a claim can explain itself later. Tidying
the vocabulary by removing rows would destroy exactly what the design exists to keep.
So every claim is *moved* — same id, same content, same lifecycle — and the move is
recorded in the audit trail as a ``MIGRATE_PREDICATE`` operation, which answers "why
was this claim's predicate changed?" months from now.

**Why it is not a knowledge edit.** ADR-009 forbids editing a claim's content: a
correction creates a *new* claim plus a relation. This is a different act — a
registry migration — and it is why the repository methods it uses are grouped under
"registry migration" rather than beside `insert`.

**Nothing is guessed.** A stored value that maps to no registered predicate is
reported under NEEDS REVIEW and left untouched.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Move legacy claims onto registered predicates.')
    parser.add_argument('--apply', action='store_true',
                        help='execute the plan (default: report only, write nothing)')
    parser.add_argument('--database',
                        help='SQLite file to migrate (default: the configured database)')
    return parser


def _bootstrap(argv) -> argparse.Namespace:
    """Resolve the target database **before anything imports the config**.

    ``app.config.DEFAULTS`` reads ``DATABASE_PATH`` at import time, and
    ``get_settings()`` then lets ``data/settings.json`` *override* it — so an
    operator's environment variable can be silently ignored and a migration run
    against a database nobody asked for. That failure mode is unacceptable here, so
    an explicit ``--database`` is applied (and the settings file moved out of the
    way) before the first ``import app.*``. After that the answer is fixed, and the
    report prints it as an absolute path so the target is never in doubt.
    """
    args = _parser().parse_args(argv)
    if args.database:
        target = Path(args.database).resolve()
        os.environ['DATABASE_PATH'] = str(target)
        os.environ['SETTINGS_PATH'] = str(target.with_suffix('.no-settings.json'))
    return args


ARGS = _bootstrap(None)          # module scope on purpose: env first, imports second

from app.config import runtime                                        # noqa: E402
from app.db import loads, transaction                                 # noqa: E402
from app.domain.predicate_migration import MIGRATE, plan_migrations   # noqa: E402
from app.repositories.claim_repo import ClaimRepository               # noqa: E402
from app.repositories.operation_repo import OperationRepository       # noqa: E402

AUDIT_KIND = 'MIGRATE_PREDICATE'


def target_database() -> Path:
    """The file this run will actually touch, resolved — never a relative guess."""
    return Path(runtime()['database_path']).resolve()


def render(plans, census: dict[str, int]) -> str:
    """The report. Read top-down: what will move, then what needs a human."""
    migrating = [p for p in plans if p.migratable]
    review = [p for p in plans if p.action != MIGRATE]
    lines = [
        'Predicate Hygiene Report',
        f'  database: {target_database()}',
        f'  {sum(census.values())} claims across {len(census)} stored predicate values',
        '',
        f'  MIGRATE ({len(migrating)})',
    ]
    lines += ['    (nothing to migrate)'] if not migrating else [
        f'    {p.legacy:<18} x{p.claims:<4} -> {p.canonical}'
        f'{" + temporal_signal=" + p.temporal_signal if p.temporal_signal else ""}'
        f'   ({p.reason}, {p.confidence:.0%})'
        for p in migrating]
    lines += ['', f'  NEEDS REVIEW ({len(review)})']
    lines += ['    (nothing)'] if not review else [
        f'    {p.legacy:<18} x{p.claims:<4} -> UNRESOLVED   ({p.reason})' for p in review]
    return '\n'.join(lines)


def apply_plan(plans) -> dict:
    """Execute the migratable plans in **one** transaction, auditing every claim.

    One transaction for the whole run: a half-migrated vocabulary is worse than an
    unmigrated one, because the two halves would disagree about which term means
    what — the exact failure this migration exists to remove.
    """
    moved = records = 0
    with transaction() as conn:
        claims = ClaimRepository(conn)
        operations = OperationRepository(conn)
        for plan in plans:
            if not plan.migratable:
                continue
            for row in claims.claims_with_predicate(plan.legacy):
                context = loads(row.get('context_json') or '{}', {}) or {}
                if plan.temporal_signal:
                    # The signal left the predicate name, so it has to land where
                    # time belongs: as context, never as vocabulary.
                    context.setdefault('temporal_signal', plan.temporal_signal)
                claims.set_predicate(row['id'], plan.canonical, context=context)
                operations.record(
                    op_id=str(uuid.uuid4()), kind=AUDIT_KIND, actor='migration',
                    status='applied', reason=f'legacy invalid predicate: {plan.legacy}',
                    payload={'claim_id': row['id'], 'from': plan.legacy, 'to': plan.canonical,
                             'temporal_signal': plan.temporal_signal, 'source': plan.source},
                    result={'predicate': plan.canonical})
                moved += 1
                records += 1
    return {'moved': moved, 'records': records}


def main() -> int:
    """Report first — the plan and the target are always printed before any write."""
    census = ClaimRepository().predicate_census()
    plans = plan_migrations(census)
    print(render(plans, census))
    if not ARGS.apply:
        print('\n  DRY RUN — nothing was written. Re-run with --apply to execute.')
        return 0
    result = apply_plan(plans)
    print(f'\n  APPLIED — moved={result["moved"]} audit_records={result["records"]} '
          f'(kind={AUDIT_KIND})')
    print(f'  target: {target_database()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
