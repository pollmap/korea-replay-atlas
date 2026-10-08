"""Compatibility error for retired 3D-only pipeline entrypoints. No I/O."""
MESSAGE = '3D generation is retired. Use pipeline.map_tiles_regional for the 2D map.'

def retired(*_args, **_kwargs):
    raise SystemExit(MESSAGE)
