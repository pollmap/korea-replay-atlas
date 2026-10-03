"""Explicit acquisition scope pinned to the verified 2026-09 legal-code registry.

No name matching or inferred prefix membership. A registry update requires review
of this allowlist; nationwide remains an explicit backward-compatible option.
"""
from .real_estate import RealEstateError
from .real_estate_priority import priority_map, POLICY_ID as NATIONAL_POLICY

SCOPES = ('nationwide', 'priority-nine')
FOCUS_POLICY = 'capital-daejeon-sejong-cheongju-cheonan-asan-busan-20261003'
CODE_GROUPS = (
    ('경기', ('41111','41113','41115','41117','41131','41133','41135','41150','41171','41173',
             '41192','41194','41196','41210','41220','41250','41271','41273','41281','41285',
             '41287','41290','41310','41360','41370','41390','41410','41430','41450','41461',
             '41463','41465','41480','41500','41550','41570','41591','41593','41595','41597',
             '41610','41630','41650','41670','41800','41820','41830')),
    ('서울', ('11110','11140','11170','11200','11215','11230','11260','11290','11305','11320',
             '11350','11380','11410','11440','11470','11500','11530','11545','11560','11590',
             '11620','11650','11680','11710','11740')),
    ('인천', ('28125','28155','28177','28185','28200','28237','28245','28275','28290','28710','28720')),
    ('대전', ('30110','30140','30170','30200','30230')),
    ('세종', ('36110',)),
    ('청주', ('43111','43112','43113','43114')),
    ('천안', ('44131','44133')),
    ('아산', ('44200',)),
    ('부산', ('26110','26140','26170','26200','26230','26260','26290','26320','26350','26380',
             '26410','26440','26470','26500','26530','26710')),
)
FOCUS_RANKS = {code: rank for rank, (_, codes) in enumerate(CODE_GROUPS) for code in codes}


def validate_scope(scope):
    if scope not in SCOPES:
        raise RealEstateError('invalid_collection_scope')
    return scope


def selected_regions(regions, scope='nationwide'):
    validate_scope(scope)
    selected = list(regions) if scope == 'nationwide' else [r for r in regions if r['lawd_code'] in FOCUS_RANKS]
    if not selected:
        raise RealEstateError('collection_scope_empty')
    return selected


def scope_priority(regions, scope='nationwide'):
    selected = selected_regions(regions, scope)
    return priority_map(selected) if scope == 'nationwide' else {r['lawd_code']: FOCUS_RANKS[r['lawd_code']] for r in selected}


def scope_policy(scope):
    return NATIONAL_POLICY if validate_scope(scope) == 'nationwide' else FOCUS_POLICY


def scope_condition(scope='nationwide', column='lawd_code'):
    """Fixed constants only, for identical filters on every scheduler path."""
    validate_scope(scope)
    if column not in ('lawd_code', 'j.lawd_code'):
        raise RealEstateError('invalid_collection_scope_column')
    if scope == 'nationwide':
        return ''
    return ' AND ' + column + ' IN (' + ','.join("'"+code+"'" for code in FOCUS_RANKS) + ')'


def scope_summary(regions, scope='nationwide', *, require_complete=False):
    selected = selected_regions(regions, scope)
    missing = sorted(set(FOCUS_RANKS) - {r['lawd_code'] for r in regions}) if scope != 'nationwide' else []
    if type(require_complete) is not bool or require_complete and missing:
        raise RealEstateError('collection_scope_registry_incomplete')
    return {'id': scope, 'policy': scope_policy(scope), 'region_count': len(selected),
            'expected_region_count': len(FOCUS_RANKS) if scope != 'nationwide' else len(regions),
            'missing_codes': missing,
            'registry_region_count': len(regions), 'codes': sorted(r['lawd_code'] for r in selected)}
