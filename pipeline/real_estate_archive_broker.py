"""Wire contract for the private, fixed-operation D1 broker.

No SQL or database identifier travels over this transport. The public catalog
contains server-owned statements; changing it requires deploying the Worker.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .real_estate import RealEstateError

ENDPOINT_SHA256 = '79162c2089fdacb6b2cb9994f840ebf2dccc69401d16905d3e195802d1094f80'
REQUEST_LIMIT = 160 * 1024
RESPONSE_LIMIT = 384 * 1024
CATALOG = json.loads((Path(__file__).resolve().parent.parent / 'worker' /
                      'archive-broker-operations.json').read_text(encoding='utf-8'))
OPERATIONS = {row['sql']: (name, row) for name, row in CATALOG['operations'].items()}
REMOTE_ERRORS = frozenset((
    'archive_daily_write_limit', 'archive_daily_read_limit',
    'collection_ownership_lost', 'collection_daily_budget',
    'collection_invalid_reservation', 'collection_baseline_required',
    'collection_baseline_conflict', 'archive_broker_response_limit',
))


def validate_endpoint(endpoint):
    try:
        parsed = urlsplit(endpoint)
        valid = (isinstance(endpoint, str) and parsed.scheme == 'https'
                 and parsed.hostname and not parsed.username and not parsed.password
                 and parsed.port is None and parsed.path == '/v1/query'
                 and not parsed.query and not parsed.fragment
                 and hmac.compare_digest(hashlib.sha256(endpoint.encode()).hexdigest(), ENDPOINT_SHA256))
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise RealEstateError('archive_broker_endpoint')
    return parsed.hostname


def operation(sql, params, *, object_database):
    entry = OPERATIONS.get(' '.join(sql.split()))
    if entry is None:
        raise RealEstateError('archive_broker_operation')
    name, row = entry
    if (row['database'] != ('object' if object_database else 'control')
            or len(params) != row['parameters']):
        raise RealEstateError('archive_broker_operation')
    for value in params:
        if value is None:
            continue
        if type(value) is int and abs(value) <= 2**53 - 1:
            continue
        if isinstance(value, str) and len(value.encode('utf-8')) <= 65536:
            continue
        raise RealEstateError('archive_broker_parameters')
    return name


def validate_token(token):
    if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{64}', token):
        raise RealEstateError('archive_broker_configuration')
    return token
