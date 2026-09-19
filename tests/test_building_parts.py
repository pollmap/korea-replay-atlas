from shapely.geometry import box

from pipeline.building_parts import audit_rows


BBOX = (127.0, 36.0, 127.01, 36.01)


def parent(id='p', **extra):
    return {'id': id, 'geometry': box(127.002, 36.002, 127.006, 36.006).wkb,
            'has_parts': True, **extra}


def part(id='a', **extra):
    return {'id': id, 'building_id': 'p', 'geometry': box(127.002, 36.002, 127.004, 36.006).wkb,
            'height': 15.0, **extra}


def test_explicit_height_is_preserved_without_floor_or_terrain_invention():
    candidates, audit = audit_rows([parent()], [part(min_height=4, num_floors=8)], BBOX)
    props = candidates['features'][0]['properties']
    assert props['height'] == 15 and props['min_height'] == 4
    assert props['evidence_type'] == 'source_attribute' and props['review_only']
    assert 'base_height' not in props
    assert audit['parts'][0]['containment_ratio'] > .9999
    assert not props['render_eligible'] and props['render_height'] is None
    assert props['height_semantics'] == 'unresolved'


def test_missing_height_cannot_be_filled_from_parent_or_floors():
    candidates, audit = audit_rows([parent(height=100)], [part(height=None, num_floors=9)], BBOX)
    assert not candidates['features']
    assert audit['counts']['missing_or_invalid_source_height'] == 1


def test_orphan_and_duplicate_ids_are_quarantined():
    candidates, audit = audit_rows([parent(), parent()], [part(), part(), part(id='b', building_id='missing')], BBOX)
    assert not candidates['features']
    assert audit['counts']['ambiguous_parent_id'] == 2
    assert audit['counts']['duplicate_part_ids'] == 1
    assert audit['counts']['parent_not_in_sample'] == 1


def test_noncontained_parts_and_floating_floor_only_parts_need_review():
    candidates, audit = audit_rows([parent()], [part(geometry=box(127.008, 36.008, 127.009, 36.009).wkb),
                                              part(id='b', min_floor=2)], BBOX)
    assert not candidates['features']
    assert audit['counts']['part_outside_parent_footprint'] == 1
    assert audit['counts']['floating_part_without_source_min_height'] == 1


def test_stacked_parts_are_not_rejected_as_flat_geometry_duplicates():
    candidates, audit = audit_rows([parent()], [part(), part(id='b', min_height=15, height=5)], BBOX)
    assert len(candidates['features']) == 2
    assert audit['parents'][0]['parts_footprint_coverage'] < .51
    assert not audit['parts'][1]['extrusion_candidate_pending_ground']


def test_exact_osm_part_has_known_semantics_but_no_invented_ground():
    candidates, audit = audit_rows([parent()], [part(
        id='35366230-3166-3235-B463-376261376263', height=112, min_height=100)], BBOX)
    props = candidates['features'][0]['properties']
    assert props['height_semantics'] == 'ground_to_top'
    assert props['extrusion_candidate_pending_ground']
    assert not props['render_eligible'] and 'base_height' not in props


def test_missing_parts_and_invalid_numeric_values_are_exposed():
    candidates, audit = audit_rows([parent(), parent(id='empty')],
                                  [part(height=float('nan')), part(id='b', height=True),
                                   part(id='c', min_height=float('inf'))], BBOX)
    assert not candidates['features']
    assert audit['counts']['parents_flagged_but_no_parts'] == 1
    assert audit['counts']['missing_or_invalid_source_height'] == 2
    assert audit['counts']['invalid_source_min_height'] == 1
