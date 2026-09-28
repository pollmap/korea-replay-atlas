"""Bounded, secret-free observations of one archive client's remote work.

These are reported D1 counters, not an account-wide quota or a reservation.
Unknown responses/metadata must never turn into a confirmed zero charge.
"""
from __future__ import annotations

from copy import deepcopy


def counter(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


class ArchiveUsage:
    def __init__(self, transport):
        self.transport = transport
        self.calls = 0
        self.attempts = 0
        self.outcomes = dict(success=0, local_rejected=0, response_error=0, response_unresolved=0)
        self.mutating_attempts = 0
        self.writes_unconfirmed = 0
        self.request_bytes = 0
        self.response_bytes = 0
        self.responses = 0
        self.results = 0
        self.rows = {name: {'observed': 0, 'missing_results': 0}
                     for name in ('rows_read', 'rows_written')}
        self.sizes = {name: {'first': None, 'last': None, 'maximum': None,
                            'observations': 0, 'missing_results': 0,
                            'first_after_read': None, 'baseline_before_mutations': None,
                            'last_observation_call': 0,
                            'last_mutation_call': 0, 'last_unconfirmed_call': 0}
                      for name in ('control', 'object0', 'object1', 'object2', 'object3', 'unmapped')}

    def begin(self, alias, read_only):
        self.calls += 1
        return QueryObservation(self, alias if alias in self.sizes else 'unmapped', read_only, self.calls)

    def snapshot(self):
        unverified = self.attempts - self.outcomes['success']
        rows = {}
        for name, values in self.rows.items():
            complete = unverified == 0 and values['missing_results'] == 0
            rows[name] = {**values, 'unverified_queries': unverified,
                          'total': values['observed'] if complete else None,
                          'complete': complete}
        sizes = {}
        for name, values in self.sizes.items():
            if name == 'unmapped' and not any(values[k] for k in ('observations', 'missing_results', 'last_mutation_call')):
                continue
            sizes[name] = {'first_observed_bytes': values['first'],
                           'last_observed_bytes': values['last'],
                           'maximum_observed_bytes': values['maximum'],
                           'observed_delta_bytes': (values['last'] - values['first']
                                if values['observations'] >= 2 else None),
                           'observations': values['observations'],
                           'missing_results': values['missing_results'],
                           'first_observation_after_read': values['first_after_read'],
                           'first_observation_precedes_mutations': values['baseline_before_mutations'],
                           'last_observation_covers_mutations': bool(values['observations']
                                and values['last_observation_call'] >= values['last_mutation_call']
                                and values['last_observation_call'] > values['last_unconfirmed_call'])}
        return {'schema_version': 1, 'available': True, 'transport': self.transport,
                'scope': 'archive-client-lifetime-not-account-quota',
                'query_calls': self.calls, 'transport_attempts': self.attempts,
                'outcomes': dict(self.outcomes),
                'in_flight': self.calls - sum(self.outcomes.values()),
                'mutating_attempts': self.mutating_attempts,
                'writes_unconfirmed': self.writes_unconfirmed,
                'request_body_bytes_attempted': self.request_bytes,
                'response_body_bytes_observed': self.response_bytes,
                'responses_observed': self.responses, 'sql_results_observed': self.results,
                **rows, 'database_sizes': sizes}


class QueryObservation:
    def __init__(self, meter, alias, read_only, sequence):
        self.meter = meter
        self.alias = alias
        self.read_only = read_only
        self.sequence = sequence
        self.sent = False
        self.explicit_error = False
        self.results = 0

    def request(self, size):
        self.sent = True
        self.meter.attempts += 1
        self.meter.request_bytes += size
        if not self.read_only:
            self.meter.mutating_attempts += 1
            self.meter.sizes[self.alias]['last_mutation_call'] = self.sequence

    def response(self, size):
        self.meter.responses += 1
        self.meter.response_bytes += size

    def result(self, result):
        self.results += 1
        self.meter.results += 1
        meta = result.get('meta') if isinstance(result, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        for name, values in self.meter.rows.items():
            if counter(meta.get(name)):
                values['observed'] += meta[name]
            else:
                values['missing_results'] += 1
        values = self.meter.sizes[self.alias]
        size = meta.get('size_after')
        if not counter(size):
            values['missing_results'] += 1
            return
        if values['first'] is None:
            values['first'] = size
            values['first_after_read'] = self.read_only
            values['baseline_before_mutations'] = values['last_mutation_call'] == 0
        values['last'] = size
        values['maximum'] = max(values['maximum'] or 0, size)
        values['observations'] += 1
        values['last_observation_call'] = self.sequence

    def finish(self, success):
        if success:
            outcome = 'success'
            if not self.results:
                # An accepted response without any result metadata is unknown.
                for values in self.meter.rows.values():
                    values['missing_results'] += 1
                self.meter.sizes[self.alias]['missing_results'] += 1
        elif not self.sent:
            outcome = 'local_rejected'
        else:
            outcome = 'response_error' if self.explicit_error else 'response_unresolved'
            if not self.read_only:
                self.meter.writes_unconfirmed += 1
                self.meter.sizes[self.alias]['last_unconfirmed_call'] = self.sequence
        self.meter.outcomes[outcome] += 1


def usage_report(store):
    meter = getattr(store, '_usage', None)
    if not isinstance(meter, ArchiveUsage):
        return {'schema_version': 1, 'available': False, 'reason': 'client-not-instrumented'}
    return deepcopy(meter.snapshot())
