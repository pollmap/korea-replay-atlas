"""Interpret explicit OSM lifecycle tags without asserting official project status.

An existing railway tag describes a mapped track, not a promise that passenger
service is operating. Proposed/construction geometry must never enter routing or
station-depth interpolation as existing track.
"""

INACTIVE = ('abandoned', 'disused', 'razed', 'demolished', 'removed')


def _enabled(value):
    return value is not None and str(value).strip().lower() not in ('', 'no', 'false', '0')


def rail_lifecycle(tags):
    state = str(tags.get('state', '')).strip().lower()
    railway = str(tags.get('railway', '')).strip().lower()
    for lifecycle in INACTIVE:
        if railway == lifecycle or state == lifecycle or _enabled(tags.get(lifecycle)) or _enabled(tags.get(lifecycle + ':railway')):
            return 'inactive'
    for lifecycle in ('construction', 'proposed'):
        if railway == lifecycle or state == lifecycle or _enabled(tags.get(lifecycle)) or _enabled(tags.get(lifecycle + ':railway')):
            return lifecycle
    if state in ('temporary', 'suspended', 'inactive'):
        return 'unknown'
    return 'existing'
