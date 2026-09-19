"""Keep rendered building parts separate from declared logical source buildings."""

BUILDING_COUNT_FIELDS = (
    'logical_source_building_count', 'building_parts_parent_count',
    'building_parts_feature_count', 'building_parts_publication_version',
)


def building_count_contract(asset):
    """Validate optional publication metadata; legacy counts remain unknown."""
    if not any(key in asset for key in BUILDING_COUNT_FIELDS):
        return None
    if asset.get('layer') != 'buildings' or asset.get('format') != '3d-tiles':
        raise ValueError('Building count contract requires a building tileset')
    counts = {}
    for key in ('count', 'feature_count', *BUILDING_COUNT_FIELDS[:3]):
        value = asset.get(key)
        if type(value) is not int or not 0 <= value <= 2**53 - 1:
            raise ValueError('Invalid building count contract: ' + key)
        counts[key] = value
    version = asset.get('building_parts_publication_version')
    if not isinstance(version, str) or not version.strip():
        raise ValueError('Invalid building count contract: publication version')
    logical = counts['logical_source_building_count']
    parents = counts['building_parts_parent_count']
    parts = counts['building_parts_feature_count']
    if (parents > logical or (parents == 0) != (parts == 0) or parts < parents
            or counts['count'] != counts['feature_count']
            or counts['count'] != logical - parents + parts):
        raise ValueError('Building count contract does not reconcile source buildings and parts')
    return {key: asset[key] for key in BUILDING_COUNT_FIELDS}
