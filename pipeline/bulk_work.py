"""Serialize this project's disk-heavy collection and candidate generation."""
from contextlib import contextmanager
from pathlib import Path
from .real_estate import RealEstateError
from .real_estate_local_archive import _lock

@contextmanager
def bulk_work(data):
    with _translate_busy():
        with _lock(Path(data) / 'bulk-work.lock'):
            yield

@contextmanager
def _translate_busy():
    try:
        yield
    except RealEstateError as error:
        if error.code == 'local_archive_writer_active':
            raise RealEstateError('vps_bulk_work_busy') from None
        raise
