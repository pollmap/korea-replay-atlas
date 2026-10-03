"""Bounded transaction packs emitted directly after source verification."""
from collections import defaultdict
from .real_estate import RealEstateError, canonical_bytes


def emit_month_packs(monthly, emit, *, release, code, target_bytes, record_id):
    refs = defaultdict(list); pack = {}; size = 0; index = 0
    envelope = {'schema_version':1, 'kind':'property-transaction-pack',
                'release_id':release, 'lawd_code':code, 'months':[]}
    base_size = len(canonical_bytes(envelope))
    def flush():
        nonlocal pack, size, index
        if not pack: return
        if index >= 10_000: raise RealEstateError('transaction_pack_asset_budget')
        value = {**envelope, 'months':[{'deal_month':month, 'transactions':rows} for month,rows in sorted(pack.items())]}
        if len(canonical_bytes(value)) > target_bytes:
            raise RealEstateError('transaction_pack_asset_budget')
        ref = emit(f'transaction-packs/{code}/{index:04d}.json', value)
        for month, rows in sorted(pack.items()):
            for trade in {r['trade_type'] for r in rows}: refs[(month,trade)].append(ref)
            for row in rows: record_id(row['id'])
        pack = {}; size = 0; index += 1
    for month, rows in sorted(monthly.items()):
        for row in rows:
            row_size = len(canonical_bytes(row))
            overhead = len(canonical_bytes({'deal_month':month,'transactions':[]})) + (1 if pack else 0) if month not in pack else 1
            if base_size + size + overhead + row_size > target_bytes:
                flush(); overhead = len(canonical_bytes({'deal_month':month,'transactions':[]}))
            if base_size + overhead + row_size > target_bytes:
                raise RealEstateError('transaction_pack_single_row_budget')
            pack.setdefault(month, []).append(row); size += overhead + row_size
    flush()
    return refs
