"""Remote ownership and conservative per-service call accounting for collection.

Every request is reserved in D1 before contacting MOLIT. Uncertain reservations
remain charged. Expired owners are never replaced automatically.
"""
from __future__ import annotations

import re
import sqlite3
import uuid

from .real_estate import RealEstateError
from .real_estate_fetch import fetch_page, assert_no_secret

LEASE_SECONDS = 900
DAILY_LIMIT = 8000
NOW = "CAST(strftime('%s','now') AS INTEGER)"
DAY = "strftime('%Y-%m-%d','now','+9 hours')"


class CollectionGuard:
    def __init__(self, archive):
        self.archive = archive

    def query(self, sql, params=()):
        return self.archive.query(self.archive.control, sql, params)

    def initialize(self):
        statements = [
            "CREATE TABLE IF NOT EXISTS collection_owner (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, generation INTEGER NOT NULL, expires INTEGER NOT NULL, base_digest TEXT, base_bytes INTEGER)",
            "INSERT OR IGNORE INTO collection_owner(id,generation,expires) VALUES(1,0,0)",
            "CREATE TABLE IF NOT EXISTS collection_budget (day TEXT NOT NULL, trade TEXT NOT NULL CHECK(trade IN ('sale','rent')), used INTEGER NOT NULL CHECK(used>=0 AND used<=8000), PRIMARY KEY(day,trade))",
            "CREATE TABLE IF NOT EXISTS collection_reservations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, generation INTEGER NOT NULL, day TEXT NOT NULL, trade TEXT NOT NULL CHECK(trade IN ('sale','rent')), job TEXT NOT NULL, page INTEGER NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('reserved','stored','failed')), raw_digest TEXT, raw_bytes INTEGER, error_code TEXT)",
            "CREATE INDEX IF NOT EXISTS collection_pending ON collection_reservations(owner,generation,phase)",
            # These triggers and the reservation insert are one SQLite statement:
            # no check-then-increment race and no COUNT scan of the day's calls.
            # Parenthesized CASE avoids D1 REST statement splitter issue #4727.
            f"""CREATE TRIGGER IF NOT EXISTS collection_reserve_check BEFORE INSERT ON collection_reservations
              WHEN NOT EXISTS(SELECT 1 FROM collection_reservations WHERE id=NEW.id)
              BEGIN
                SELECT (CASE WHEN NEW.day!={DAY} OR NEW.phase!='reserved' OR NEW.page<1 OR NEW.page>1000
                  THEN RAISE(ABORT,'collection_invalid_reservation') END);
                SELECT (CASE WHEN NOT EXISTS(SELECT 1 FROM collection_owner WHERE id=1 AND owner=NEW.owner
                  AND generation=NEW.generation AND expires>{NOW})
                  THEN RAISE(ABORT,'collection_ownership_lost') END);
                SELECT (CASE WHEN COALESCE((SELECT used FROM collection_budget WHERE day=NEW.day AND trade=NEW.trade),0)>=8000
                  THEN RAISE(ABORT,'collection_daily_budget') END);
              END;""",
            """CREATE TRIGGER IF NOT EXISTS collection_reserve_charge AFTER INSERT ON collection_reservations
              BEGIN
                INSERT INTO collection_budget(day,trade,used) VALUES(NEW.day,NEW.trade,1)
                  ON CONFLICT(day,trade) DO UPDATE SET used=used+1;
              END;""",
        ]
        for sql in statements: self.query(sql)

    def acquire(self, expected_head):
        if not isinstance(expected_head,dict) or not re.fullmatch('[a-f0-9]{64}',str(expected_head.get('sha256',''))):
            raise RealEstateError('collection_backup_required')
        owner=uuid.uuid4().hex
        result=self.query(f"""UPDATE collection_owner SET owner=?,generation=generation+1,
            expires={NOW}+?,base_digest=?,base_bytes=? WHERE id=1 AND owner IS NULL
            AND EXISTS(SELECT 1 FROM backup_heads WHERE name='collector' AND digest=? AND bytes=?)
            RETURNING owner,generation,expires""",
            [owner,LEASE_SECONDS,expected_head['sha256'],expected_head['bytes'],expected_head['sha256'],expected_head['bytes']])
        if len(result['results'])!=1:
            raise RealEstateError('collection_busy_or_backup_changed')
        return result['results'][0]

    def renew(self, lease):
        result=self.query(f"""UPDATE collection_owner SET expires={NOW}+? WHERE id=1 AND owner=?
            AND generation=? AND expires>{NOW} RETURNING expires""",[LEASE_SECONDS,lease['owner'],lease['generation']])
        if len(result['results'])!=1:raise RealEstateError('collection_ownership_lost')

    def reserve(self, lease, trade, job, page, *, reservation_id=None):
        if (trade not in ('sale','rent') or not isinstance(job,str)
                or not re.fullmatch(trade+r'/[0-9]{5}/[0-9]{4}(?:0[1-9]|1[0-2])',job)
                or type(page) is not int or not 1<=page<=1000):
            raise RealEstateError('collection_invalid_request')
        reservation_id=reservation_id or uuid.uuid4().hex
        if not re.fullmatch('[a-f0-9]{32}',reservation_id):raise RealEstateError('collection_invalid_reservation')
        result=self.query(f"""INSERT OR IGNORE INTO collection_reservations
            (id,owner,generation,day,trade,job,page,phase) VALUES(?,?,?,{DAY},?,?,?,'reserved')
            RETURNING id,day""",[reservation_id,lease['owner'],lease['generation'],trade,job,page])
        # A repeated ID may be an uncertain prior request: never authorize another
        # upstream call using it, even though the budget insert is idempotent.
        if not result['results']:raise RealEstateError('collection_reservation_exists_do_not_call')
        return result['results'][0]

    def finish(self, lease, reservation_id, *, raw=None, error=None):
        if (raw is None)==(error is None):raise RealEstateError('collection_result_required')
        active=self.query(f"""SELECT id FROM collection_reservations WHERE id=? AND owner=? AND generation=? AND phase='reserved'
            AND EXISTS(SELECT 1 FROM collection_owner WHERE id=1 AND owner=? AND generation=? AND expires>{NOW})""",
            [reservation_id,lease['owner'],lease['generation'],lease['owner'],lease['generation']])
        if len(active['results'])!=1:raise RealEstateError('collection_result_not_committed')
        if raw is not None:
            if not isinstance(raw,bytes) or not 0<len(raw)<=8*1024**2:raise RealEstateError('collection_response_size')
            # The original body survives even if the local runner disappears after
            # this method. A timeout never refunds its reserved provider call.
            descriptor=self.archive.put(raw); phase='stored'
        else:
            if not isinstance(error,str) or not re.fullmatch('[a-z_]{1,64}',error):raise RealEstateError('collection_error_code')
            descriptor={'sha256':None,'bytes':None}; phase='failed'
        result=self.query(f"""UPDATE collection_reservations SET phase=?,raw_digest=?,raw_bytes=?,error_code=?
            WHERE id=? AND owner=? AND generation=? AND phase='reserved'
            AND EXISTS(SELECT 1 FROM collection_owner WHERE id=1 AND owner=? AND generation=? AND expires>{NOW})
            RETURNING id""",[phase,descriptor['sha256'],descriptor['bytes'],error,reservation_id,
                lease['owner'],lease['generation'],lease['owner'],lease['generation']])
        if len(result['results'])!=1:raise RealEstateError('collection_result_not_committed')
        return descriptor

    def release(self, lease, verified_head):
        result=self.query(f"""UPDATE collection_owner SET owner=NULL,expires=0 WHERE id=1
            AND owner=? AND generation=? AND expires>{NOW}
            AND EXISTS(SELECT 1 FROM backup_heads WHERE name='collector' AND digest=? AND bytes=?)
            AND NOT EXISTS(SELECT 1 FROM collection_reservations WHERE owner=? AND generation=? AND phase='reserved')
            AND (base_digest!=? OR NOT EXISTS(SELECT 1 FROM collection_reservations WHERE owner=? AND generation=?))
            RETURNING generation""",[lease['owner'],lease['generation'],verified_head['sha256'],verified_head['bytes'],lease['owner'],lease['generation'],verified_head['sha256'],lease['owner'],lease['generation']])
        if len(result['results'])!=1:raise RealEstateError('collection_recovery_required')

    def status(self):
        return {'owner':self.query(f"SELECT generation,owner IS NOT NULL AS occupied,expires,expires<={NOW} AS expired FROM collection_owner WHERE id=1")['results'],
                'today':self.query(f'SELECT trade,used FROM collection_budget WHERE day={DAY} ORDER BY trade')['results'],
                'daily_limit_per_service':DAILY_LIMIT}


def guarded_transport(guard, lease, transport=fetch_page):
    """Adapter for Collector: remote reservation → official fetch → durable body.

    A remote persistence failure must stop the local collector immediately. It
    cannot be downgraded to a missing month or retried as an ordinary API error.
    """
    def fetch(key, trade, code, month, page, page_size, *, timeout, max_bytes):
        try:
            guard.renew(lease)
            reservation=guard.reserve(lease,trade,f'{trade}/{code}/{month}',page)
        except (RealEstateError,sqlite3.Error):
            raise RealEstateError('remote_checkpoint_error') from None
        try:
            raw=transport(key,trade,code,month,page,page_size,timeout=timeout,max_bytes=max_bytes)
            if not isinstance(raw,bytes) or not 0<len(raw)<=max_bytes:
                raise RealEstateError('response_size_limit')
            assert_no_secret(raw,key)
        except RealEstateError as error:
            try:guard.finish(lease,reservation['id'],error=error.code)
            except (RealEstateError,sqlite3.Error):raise RealEstateError('remote_checkpoint_error') from None
            raise
        try:guard.finish(lease,reservation['id'],raw=raw)
        except (RealEstateError,sqlite3.Error):raise RealEstateError('remote_checkpoint_error') from None
        return raw
    return fetch
