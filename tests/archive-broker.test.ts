import { DatabaseSync } from 'node:sqlite';
import { afterEach, describe, expect, it } from 'vitest';
import { archiveBrokerFetch, REQUEST_LIMIT, RESPONSE_LIMIT, type ArchiveBinding, type ArchiveBrokerEnv } from '../worker/archive-broker';
import catalog from '../worker/archive-broker-operations.json';

const TOKEN = 'f'.repeat(64);
const databases: DatabaseSync[] = [];
afterEach(() => { for (const db of databases.splice(0)) db.close(); });

function setup() {
  const raw = Array.from({ length: 5 }, () => { const db = new DatabaseSync(':memory:'); databases.push(db); return db; });
  const seen: string[] = [];
  const bindings: ArchiveBinding[] = raw.map(db => ({
    prepare(sql) {
      seen.push(sql);
      return { bind(...params) { return { async all() {
        const before = Number(db.prepare('SELECT total_changes() AS n').get()?.n);
        const results = db.prepare(sql).all(...params);
        const changes = Number(db.prepare('SELECT total_changes() AS n').get()?.n) - before;
        return { success: true, results, meta: { changes, size_after: 65536, rows_read: results.length, rows_written: changes } };
      } }; } };
    },
  }));
  const env: ArchiveBrokerEnv = { PROPERTY_ARCHIVE_BROKER_TOKEN: TOKEN,
    ARCHIVE_CONTROL: bindings[0], ARCHIVE_OBJECT_0: bindings[1], ARCHIVE_OBJECT_1: bindings[2],
    ARCHIVE_OBJECT_2: bindings[3], ARCHIVE_OBJECT_3: bindings[4] };
  return { env, raw, seen };
}
function request(payload: unknown, headers: Record<string, string> = {}, method = 'POST', path = '/v1/query') {
  return new Request(`https://fixture.example.invalid${path}`, { method,
    headers: { authorization: `Bearer ${TOKEN}`, 'content-type': 'application/json', ...headers },
    ...(method === 'GET' ? {} : { body: JSON.stringify(payload) }),
  });
}
function operation(prefix: string) {
  const found = Object.entries(catalog.operations).filter(([, row]) => row.sql.startsWith(prefix));
  expect(found).toHaveLength(1);
  return found[0][0];
}
function payload(prefix: string, params: (string | number | null)[] = [], database = 'control') {
  return { version: 1, operation: operation(prefix), params, database };
}
async function initialize(env: ArchiveBrokerEnv) {
  // Table order precedes trigger/index creation; every call still goes through
  // the real authenticated handler and its fixed SQL catalog.
  for (const prefix of ['CREATE TABLE', 'CREATE INDEX', 'CREATE TRIGGER', 'INSERT OR IGNORE INTO collection_owner']) {
    for (const [id, row] of Object.entries(catalog.operations).filter(([, row]) => row.sql.startsWith(prefix))) {
      for (const database of row.database === 'control' ? ['control'] : ['object0', 'object1', 'object2', 'object3']) {
        const response = await archiveBrokerFetch(request({ version: 1, operation: id, params: [], database }), env);
        expect(await response.text()).not.toContain('archive_broker_query');
        expect(response.status).toBe(200);
      }
    }
  }
}

describe('private archive fixed-operation Worker', () => {
  it('requires an installed secret and matching authentication before body or D1 access', async () => {
    const { env, seen } = setup();
    for (const token of [undefined, '', 'short']) {
      const response = await archiveBrokerFetch(request({}), { ...env, PROPERTY_ARCHIVE_BROKER_TOKEN: token });
      expect(response.status).toBe(503);
    }
    const denied = await archiveBrokerFetch(request({}, { authorization: 'Bearer incorrect' }), env);
    expect(denied.status).toBe(401);
    expect(seen).toEqual([]);
    expect(denied.headers.get('cache-control')).toBe('no-store');
    expect(denied.headers.get('access-control-allow-origin')).toBeNull();
    expect(await denied.text()).not.toContain(TOKEN);
  });

  it('rejects arbitrary SQL, unexpected aliases, browser origins and unknown operations', async () => {
    const { env, seen } = setup();
    const valid = payload("SELECT digest,bytes FROM backup_heads WHERE name='collector'");
    for (const bad of [{ ...valid, sql: 'DELETE FROM collection_owner' }, { ...valid, operation: 'DROP TABLE backup_heads' },
      { ...valid, database: 'other' }, { ...valid, database: '__proto__' }, { ...valid, operation: 'constructor' },
      { ...valid, database: 'object0' }, { ...valid, params: [1] }, { ...valid, version: 2 }]) {
      expect((await archiveBrokerFetch(request(bad), env)).status).toBe(400);
    }
    expect((await archiveBrokerFetch(request(valid, { origin: 'https://public.example.invalid' }), env)).status).toBe(400);
    expect((await archiveBrokerFetch(request(valid, {}, 'OPTIONS'), env)).status).toBe(404);
    expect((await archiveBrokerFetch(request(valid, {}, 'GET'), env)).status).toBe(404);
    expect((await archiveBrokerFetch(request(valid, {}, 'POST', '/v1/query?anything'), env)).status).toBe(404);
    expect(seen).toEqual([]);
  });

  it('rejects request and parameter overflow before executing SQL', async () => {
    const { env, seen } = setup();
    expect((await archiveBrokerFetch(request('x'.repeat(REQUEST_LIMIT)), env)).status).toBe(400);
    const base = payload('SELECT part FROM archive_chunks', [], 'object0');
    for (const params of [[true], [{}], [1.5], [Number.MAX_SAFE_INTEGER + 1], ['가'.repeat(65536)], ['x'.repeat(65537)]]) {
      expect((await archiveBrokerFetch(request({ ...base, params }), env)).status).toBe(400);
    }
    expect(seen).toEqual([]);
  });

  it('preserves immutable chunks, bounded reads and the five database aliases', async () => {
    const { env, raw } = setup(); await initialize(env);
    const insert = Object.entries(catalog.operations).find(([, row]) => row.sql.startsWith('INSERT OR IGNORE INTO archive_chunks') && row.parameters === 6)![0];
    const digest = 'a'.repeat(64);
    for (let part = 0; part < 6; part += 2) {
      const response = await archiveBrokerFetch(request({ version: 1, operation: insert, database: 'object2',
        params: [digest, part, 'b'.repeat(65536), digest, part + 1, 'c'.repeat(65536)] }), env);
      expect(response.status).toBe(200);
    }
    const result = await archiveBrokerFetch(request(payload('SELECT part,payload FROM archive_chunks', [digest, 0], 'object2')), env);
    const json = await result.json() as { result: { results: { part: number; payload: string }[]; meta: { size_after: number } } };
    expect(json.result.results.map(row => row.part)).toEqual([0, 1, 2, 3]);
    expect(json.result.meta.size_after).toBe(65536);
    expect(raw[3].prepare('SELECT COUNT(*) AS n FROM archive_chunks').get()?.n).toBe(6);
    expect(raw[1].prepare('SELECT COUNT(*) AS n FROM archive_chunks').get()?.n).toBe(0);
    // An injection-like bound value remains data, never a second statement.
    const literal = "'; DROP TABLE archive_chunks; --";
    const read = await archiveBrokerFetch(request(payload('SELECT part FROM archive_chunks', [literal], 'object2')), env);
    expect(read.status).toBe(200);
    expect(raw[3].prepare('SELECT COUNT(*) AS n FROM archive_chunks').get()?.n).toBe(6);
  });

  it('preserves guard ownership, reservation triggers, quota and head CAS through actual SQLite', async () => {
    const { env, raw } = setup(); await initialize(env);
    const head = 'a'.repeat(64), owner = 'b'.repeat(32), next = 'c'.repeat(64);
    const invoke = async (prefix: string, params: (string | number | null)[]) => {
      const response = await archiveBrokerFetch(request(payload(prefix, params)), env);
      return { status: response.status, body: await response.json() as { error?: string; result?: { results: Record<string, unknown>[]; meta: { changes: number } } } };
    };
    const freeHead = Object.entries(catalog.operations).find(([, row]) => row.sql.startsWith('INSERT OR IGNORE INTO backup_heads') && row.parameters === 2)![0];
    expect((await archiveBrokerFetch(request({ version: 1, operation: freeHead, database: 'control', params: [head, 3] }), env)).status).toBe(200);
    const lease = await invoke('UPDATE collection_owner SET owner=?', [owner, 900, head, 3, head, 3]);
    expect(lease.body.result?.results[0].generation).toBe(1);
    expect((await invoke('INSERT OR IGNORE INTO collection_reservations', ['d'.repeat(32), owner, 1, 'sale', 'sale/11110/202609', 1])).body.error).toBe('collection_baseline_required');
    expect((await invoke('INSERT OR IGNORE INTO collection_baseline(id', [head, 'f'.repeat(64), owner, 1])).status).toBe(200);
    const reserve = await invoke('INSERT OR IGNORE INTO collection_reservations', ['d'.repeat(32), owner, 1, 'sale', 'sale/11110/202609', 1]);
    expect(reserve.body.result?.results).toHaveLength(1);
    const release = await invoke('UPDATE collection_owner SET owner=NULL', [owner, 1, head, 3, owner, 1, head, owner, 1]);
    expect(release.body.result?.results).toHaveLength(0);
    raw[0].prepare("UPDATE collection_budget SET used=8000 WHERE trade='sale'").run();
    const quota = await invoke('INSERT OR IGNORE INTO collection_reservations', ['e'.repeat(32), owner, 1, 'sale', 'sale/11110/202609', 2]);
    expect(quota.body.error).toBe('collection_daily_budget');
    const headOp = Object.entries(catalog.operations).find(([, row]) => row.sql.startsWith('UPDATE backup_heads') && row.parameters === 6)![0];
    const stale = await archiveBrokerFetch(request({ version: 1, operation: headOp, database: 'control', params: [next, 4, head, 3, owner, 2] }), env);
    expect(((await stale.json()) as { result: { meta: { changes: number } } }).result.meta.changes).toBe(0);
    expect(raw[0].prepare('SELECT digest FROM backup_heads').get()?.digest).toBe(head);
  });

  it('sanitizes quota/native errors and caps oversized results without retrying', async () => {
    const { env } = setup();
    let calls = 0;
    const make = (all: () => Promise<unknown>): ArchiveBinding => ({ prepare() { return { bind() { return { all }; } }; } });
    for (const [message, code] of [["D1_ERROR: exceeded D1's free tier daily row write limit", 'archive_daily_write_limit'],
      ["D1_ERROR: exceeded D1's free tier daily row read limit", 'archive_daily_read_limit'],
      ['private SQL secret database path', 'archive_broker_query']]) {
      const response = await archiveBrokerFetch(request(payload("SELECT digest,bytes FROM backup_heads WHERE name='collector'")),
        { ...env, ARCHIVE_CONTROL: make(async () => { calls++; throw new Error(message); }) });
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ success: false, error: code });
    }
    expect(calls).toBe(3);
    const huge = await archiveBrokerFetch(request(payload("SELECT digest,bytes FROM backup_heads WHERE name='collector'")),
      { ...env, ARCHIVE_CONTROL: make(async () => { calls++; return { success: true, results: [{ large: 'x'.repeat(RESPONSE_LIMIT) }], meta: {} }; }) });
    expect(huge.status).toBe(502);
    expect(calls).toBe(4);
    expect(await huge.json()).toEqual({ success: false, error: 'archive_broker_response_limit' });
  });
});
