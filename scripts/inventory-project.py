"""Summarize this project's logical file sizes. Read-only: never removes or writes files."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat


def is_link(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    )


def checked_root(expected: Path | None = None) -> Path:
    lexical = Path(__file__).absolute().parent.parent
    for item in (lexical, *lexical.parents):
        if is_link(item.lstat()):
            raise ValueError('Project root must not traverse a symlink, junction, or reparse point')
    root = lexical.resolve(strict=True)
    if expected is not None and expected.absolute().resolve(strict=True) != root:
        raise ValueError('Expected project root does not match the script location')
    marker = root / 'package.json'
    if is_link(marker.lstat()) or json.loads(marker.read_text(encoding='utf-8')).get('name') != 'korea-replay':
        raise ValueError('This inventory is restricted to the korea-replay project')
    return root


def category(relative: Path) -> str:
    parts = relative.parts
    if len(parts) == 1:
        return '(project root files)'
    if parts[0] == '.local' or parts[:2] == ('public', 'data'):
        return '/'.join(parts[:2])
    return parts[0]


def inventory(root: Path) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    buckets, errors = {}, Counter()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            info = directory.lstat()
            if is_link(info):
                errors['DirectoryBecameLink'] += 1
                continue
            if not directory.resolve(strict=True).is_relative_to(root):
                errors['DirectoryEscapedRoot'] += 1
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    item = Path(entry.path)
                    relative = item.relative_to(root)
                    bucket = buckets.setdefault(category(relative), {
                        'files': 0, 'logical_bytes': 0, 'directories': 0,
                        'skipped_links': 0, 'other_entries': 0, 'errors': 0,
                    })
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if is_link(info):
                            bucket['skipped_links'] += 1
                        elif stat.S_ISDIR(info.st_mode):
                            bucket['directories'] += 1
                            pending.append(item)
                        elif stat.S_ISREG(info.st_mode):
                            bucket['files'] += 1
                            bucket['logical_bytes'] += info.st_size
                        else:
                            bucket['other_entries'] += 1
                    except OSError as error:
                        bucket['errors'] += 1
                        errors[type(error).__name__] += 1
        except OSError as error:
            errors[type(error).__name__] += 1
    return {
        'schema_version': 1, 'operation': 'inventory-only', 'project_root': str(root),
        'started_at': started_at, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'scan_complete': not errors, 'errors': dict(errors),
        'notes': ['No file contents are read except package.json; no files are written or removed.',
                  'Symlinks, junctions, and other reparse points are counted but never followed.',
                  'Logical bytes are not a physical free-space estimate; hardlinks may be counted repeatedly.',
                  'This is an inventory taken over time, not an atomic backup or deletion allowlist.'],
        'totals': {key: sum(bucket[key] for bucket in buckets.values())
                   for key in ('files', 'logical_bytes', 'directories', 'skipped_links', 'other_entries', 'errors')},
        'categories': dict(sorted(buckets.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-root', type=Path, help='Optional exact root confirmation; mismatch refuses the scan')
    args = parser.parse_args()
    result = inventory(checked_root(args.expected_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['scan_complete']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
