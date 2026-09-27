"""Official publication lower bounds, not a claim of complete API coverage."""
from .real_estate import RealEstateError, validate_property_type, validate_scope

COMPLETED_HISTORY_MONTHS = 240
DEFAULT_PLAN_MONTHS = COMPLETED_HISTORY_MONTHS + 1
POLICY_ID = 'molit-publication-start-20260927'
SOURCE_URL = 'https://rt.molit.go.kr/pt/info/info.do?mobileAt='
FIRST_MONTHS = {
    'apartment': {'sale': '200601', 'rent': '201101'},
    'officetel': {'sale': '200601', 'rent': '201101'},
}


def source_start(property_type, trade_type):
    validate_property_type(property_type)
    if trade_type not in ('sale', 'rent'):
        raise RealEstateError('invalid_trade_type')
    return FIRST_MONTHS[property_type][trade_type]


def before_source(property_type, trade_type, month):
    validate_scope('11110', month)
    return month < source_start(property_type, trade_type)


def source_policy(property_type='apartment'):
    validate_property_type(property_type)
    return {'id': POLICY_ID, 'source_url': SOURCE_URL, 'checked_at': '2026-09-27',
            'property_type': property_type, 'first_months': dict(FIRST_MONTHS[property_type]),
            'rent_reporting_start': '202106',
            'scope': 'publication_lower_bound_not_verified_api_completeness',
            'before_source_status': 'source_unavailable'}
