"""Bound regenerated read-only DB copies after the live API adopts their successor."""
import json
from pathlib import Path
import re
import time

from .real_estate import RealEstateError, _reject_links
from .property_read_model import ReadModel, sha256


def reclaim_generations(data, *, acknowledged_generation, keep=3, grace_seconds=3600, now=None):
    if type(keep) is not int or keep < 3 or type(grace_seconds) is not int or grace_seconds < 3600:
        raise RealEstateError('read_model_retention_policy')
    folder = Path(data).absolute() / 'read-model'; _reject_links(folder)
    current_path, manifest = ReadModel(data).resolve()
    generation = manifest['generation']
    if acknowledged_generation != generation:
        raise RealEstateError('read_model_reader_not_ready')
    instant = time.time() if now is None else now
    generations = []
    for path in folder.iterdir():
        if not re.fullmatch(r'[a-f0-9]{32}-[a-f0-9]{64}\.sqlite', path.name):
            continue
        _reject_links(path)
        if path.is_file():
            generations.append(path)
    generations.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    protected = {current_path, *generations[:keep]}
    removed = []; skipped = []
    for path in generations:
        stat = path.stat()
        if path in protected or instant - stat.st_mtime < grace_seconds:
            continue
        # A new publication/rollback invalidates this cleanup decision immediately.
        _reject_links(folder / 'current.json')
        if json.loads((folder / 'current.json').read_bytes()).get('generation') != generation:
            return {'state': 'head_changed', 'deleted_files': len(removed),
                    'deleted_bytes': sum(removed), 'skipped_files': len(skipped)}
        try:
            _reject_links(path)
            if sha256(path) != path.stem[33:]:
                skipped.append(path.name); continue
            after = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                skipped.append(path.name); continue
            _reject_links(folder / 'current.json')
            if json.loads((folder / 'current.json').read_bytes()).get('generation') != generation:
                return {'state': 'head_changed', 'deleted_files': len(removed),
                        'deleted_bytes': sum(removed), 'skipped_files': len(skipped)}
            path.unlink()
            removed.append(stat.st_size)
        except OSError:
            # An open Windows reader or filesystem error leaves the copy intact.
            skipped.append(path.name)
    return {'state': 'complete', 'generation': generation, 'deleted_files': len(removed),
            'deleted_bytes': sum(removed), 'skipped_files': len(skipped),
            'keep': keep, 'grace_seconds': grace_seconds}
