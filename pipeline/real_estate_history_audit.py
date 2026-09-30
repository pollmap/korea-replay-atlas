"""Read-only, calendar-correct acquisition ledger audit for the nine regions.

Counts are region x contract month x trade tasks, not transactions, coordinates,
or published coverage. Retained snapshots waiting for refresh are kept separate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sqlite3

from .real_estate import RealEstateError, _reject_links
from .real_estate_scope import CODE_GROUPS

STATES = ('complete', 'empty', 'pending', 'partial', 'failed', 'source_unavailable')


def window(as_of):
    try:
        instant = date.fromisoformat(as_of)
    except (ValueError, TypeError):
        raise RealEstateError('history_audit_invalid_date') from None
    current = instant.year * 12 + instant.month - 1
    def month(value): return f'{value // 12:04d}{value % 12 + 1:02d}'
    return {'from': month(current - 240), 'to': month(current - 1),
            'months': 240, 'provisional_month': month(current)}


def summarize(rows, *, codes, months):
    selected = set(codes)
    counts = Counter(); retained = 0; by_trade = Counter(); slots = set()
    for row in rows:
        if row['lawd_code'] not in selected: continue
        if row['status'] not in STATES or row['trade_type'] not in ('sale', 'rent'):
            raise RealEstateError('history_audit_invalid_status')
        key = (row['lawd_code'], row['deal_month'], row['trade_type'])
        if key in slots: raise RealEstateError('history_audit_duplicate_slot')
        slots.add(key); counts[row['status']] += 1
        if row['status'] in ('complete', 'empty'):
            if not row['snapshot']: raise RealEstateError('history_audit_complete_without_snapshot')
            by_trade[row['trade_type']] += 1
        elif row['snapshot']:
            if row['status'] == 'source_unavailable':
                raise RealEstateError('history_audit_unavailable_with_snapshot')
            retained += 1
    expected = len(selected) * months * 2
    if len(slots) > expected: raise RealEstateError('history_audit_window_mismatch')
    available = expected - counts['source_unavailable']; done = counts['complete'] + counts['empty']
    return {'region_codes': len(selected), 'expected_tasks': expected, 'ledger_tasks': len(slots),
        'missing_ledger_tasks': expected - len(slots), **{state: counts[state] for state in STATES},
        'available_tasks': available, 'completed_tasks': done,
        'completed_sale_tasks': by_trade['sale'], 'completed_rent_tasks': by_trade['rent'],
        'retained_previous_snapshot_tasks': retained, 'missing_available_tasks': available - done,
        'completion_percent': round(done * 100 / available, 2) if available else None}


def audit(checkpoint, *, as_of):
    checkpoint = Path(checkpoint).absolute(); _reject_links(checkpoint)
    period = window(as_of)
    with sqlite3.connect(checkpoint.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row; db.execute('BEGIN')
        if [row[0] for row in db.execute('PRAGMA integrity_check')] != ['ok']:
            raise RealEstateError('archive_sqlite_integrity')
        rows = [dict(row) for row in db.execute(
            'SELECT lawd_code,deal_month,trade_type,status,snapshot FROM jobs WHERE deal_month BETWEEN ? AND ?',
            (period['from'], period['provisional_month']))]
    completed = [row for row in rows if row['deal_month'] <= period['to']]
    provisional = [row for row in rows if row['deal_month'] == period['provisional_month']]
    codes = tuple(code for _, group in CODE_GROUPS for code in group)
    return {'schema_version': 1, 'kind': 'property-history-acquisition-audit', 'as_of': as_of,
        'period': period, 'unit': 'region_code_x_contract_month_x_trade_type',
        'scope': 'priority-nine', 'property_type': 'apartment', 'raw_pages_reparsed': False,
        'public_release': False, 'source_calls': 0, 'ledger_writes': 0,
        'completed_window': summarize(completed, codes=codes, months=240),
        'provisional_window': summarize(provisional, codes=codes, months=1),
        'regions': [{'name': name, **summarize(completed, codes=group, months=240)} for name, group in CODE_GROUPS],
        'historical_code_coverage': 'current_codes_only_pending_effective_date_crosswalk'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', required=True, type=Path)
    parser.add_argument('--as-of', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(audit(args.checkpoint, as_of=args.as_of), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(1, 'real_estate_history_audit: ' + (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__': main()
