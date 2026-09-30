import pytest

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_history_audit import summarize, window


def test_twenty_completed_years_roll_over_but_provisional_is_separate():
    assert window('2026-10-01') == {'from': '200610', 'to': '202609', 'months': 240, 'provisional_month': '202610'}
    assert window('2026-09-30') == {'from': '200609', 'to': '202608', 'months': 240, 'provisional_month': '202609'}


def row(state, trade='sale', snapshot=None):
    return {'lawd_code': '11110', 'deal_month': '202609', 'trade_type': trade, 'status': state, 'snapshot': snapshot}


def test_unavailable_empty_refresh_and_missing_ledger_are_not_interchanged():
    report = summarize([row('empty', snapshot='descriptor'), row('source_unavailable', 'rent')], codes=['11110', '11710'], months=1)
    assert report['available_tasks'] == 3 and report['completed_tasks'] == 1
    assert report['missing_ledger_tasks'] == 2 and report['missing_available_tasks'] == 2
    report = summarize([row('partial', snapshot='previous')], codes=['11110'], months=1)
    assert report['completed_tasks'] == 0 and report['retained_previous_snapshot_tasks'] == 1


def test_duplicate_or_completed_without_snapshot_fail():
    with pytest.raises(RealEstateError, match='history_audit_duplicate_slot'):
        summarize([row('pending'), row('partial')], codes=['11110'], months=1)
    with pytest.raises(RealEstateError, match='history_audit_complete_without_snapshot'):
        summarize([row('complete')], codes=['11110'], months=1)


@pytest.mark.parametrize('value', ['2026-13-01', '2026-10', None, '2026-02-30'])
def test_invalid_asof_is_rejected(value):
    with pytest.raises(RealEstateError, match='history_audit_invalid_date'): window(value)
