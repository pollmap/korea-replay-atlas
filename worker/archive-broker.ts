/** Private archive transport. SQL comes exclusively from the deployed catalog. */
import catalog from './archive-broker-operations.json';

export const REQUEST_LIMIT = 160 * 1024;
export const RESPONSE_LIMIT = 384 * 1024;
const PARAMETER_LIMIT = 65536;
const encoder = new TextEncoder();
type BoundValue = string | number | null;
export interface ArchiveBinding {
  prepare(sql: string): { bind(...params: BoundValue[]): { all(): Promise<unknown> } };
}
export interface ArchiveBrokerEnv {
  PROPERTY_ARCHIVE_BROKER_TOKEN?: string;
  ARCHIVE_CONTROL: ArchiveBinding;
  ARCHIVE_OBJECT_0: ArchiveBinding;
  ARCHIVE_OBJECT_1: ArchiveBinding;
  ARCHIVE_OBJECT_2: ArchiveBinding;
  ARCHIVE_OBJECT_3: ArchiveBinding;
}
interface Operation { database: string; sql: string; parameters: number }
const operations: Readonly<Record<string, Operation>> = catalog.operations;
const safeErrors = ['collection_ownership_lost', 'collection_daily_budget',
  'collection_invalid_reservation', 'collection_baseline_required', 'collection_baseline_conflict'];

function reply(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: {
    'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
  } });
}
function error(code: string, status: number): Response { return reply({ success: false, error: code }, status); }
function equalToken(header: string | null, secret: string): boolean {
  const expected = `Bearer ${secret}`;
  if (header?.length !== expected.length) return false;
  let difference = 0;
  for (let i = 0; i < expected.length; i++) difference |= header.charCodeAt(i) ^ expected.charCodeAt(i);
  return difference === 0;
}
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
async function boundedBody(request: Request): Promise<unknown> {
  const length = request.headers.get('content-length');
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > REQUEST_LIMIT)) throw new Error('body');
  if (!request.body) throw new Error('body');
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const body = (async () => {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > REQUEST_LIMIT) throw new Error('body');
        chunks.push(value);
      }
      const combined = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { combined.set(chunk, offset); offset += chunk.byteLength; }
      return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(combined)) as unknown;
    })();
    return await Promise.race([body, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('body')), 5000);
    })]);
  } finally {
    clearTimeout(timer);
    // Do not wait for an untrusted client's cancellation acknowledgement.
    void reader.cancel().catch(() => undefined);
  }
}
function sanitizedError(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : '';
  if (message.includes("exceeded D1's free tier daily row write limit")) return 'archive_daily_write_limit';
  if (message.includes("exceeded D1's free tier daily row read limit")) return 'archive_daily_read_limit';
  for (const code of safeErrors) if (message.includes(code)) return code;
  return 'archive_broker_query';
}

export async function archiveBrokerFetch(request: Request, env: ArchiveBrokerEnv): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname !== '/v1/query' || url.search || url.hash || request.method !== 'POST') return error('not_found', 404);
  const secret = env.PROPERTY_ARCHIVE_BROKER_TOKEN;
  if (!secret || !/^[a-f0-9]{64}$/.test(secret)) return error('unavailable', 503);
  if (!equalToken(request.headers.get('authorization'), secret)) return error('unauthorized', 401);
  // No browser/public route, no preflight, no reflected origin or credentials.
  if (request.headers.has('origin') || request.headers.has('content-encoding')
      || request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'application/json') return error('invalid_request', 400);
  const bindings: Record<string, ArchiveBinding> = {
    control: env.ARCHIVE_CONTROL, object0: env.ARCHIVE_OBJECT_0, object1: env.ARCHIVE_OBJECT_1,
    object2: env.ARCHIVE_OBJECT_2, object3: env.ARCHIVE_OBJECT_3,
  };
  if (Object.values(bindings).some(binding => !binding || typeof binding.prepare !== 'function')) return error('unavailable', 503);
  let payload: unknown;
  try { payload = await boundedBody(request); } catch { return error('invalid_request', 400); }
  if (!object(payload) || Object.keys(payload).sort().join(',') !== 'database,operation,params,version'
      || payload.version !== 1 || typeof payload.operation !== 'string' || typeof payload.database !== 'string'
      || !Array.isArray(payload.params)) return error('invalid_request', 400);
  const op = Object.hasOwn(operations, payload.operation) ? operations[payload.operation] : undefined;
  const database = Object.hasOwn(bindings, payload.database) ? bindings[payload.database] : undefined;
  if (!op || !database || op.database !== (payload.database === 'control' ? 'control' : 'object')
      || payload.params.length !== op.parameters) return error('invalid_operation', 400);
  if (payload.params.some(value => !(value === null || (typeof value === 'number' && Number.isSafeInteger(value))
      || (typeof value === 'string' && encoder.encode(value).byteLength <= PARAMETER_LIMIT)))) return error('invalid_parameters', 400);
  try {
    // Exactly one prepared statement. Catalog triggers/CAS remain atomic in D1.
    const result = await database.prepare(op.sql).bind(...payload.params as BoundValue[]).all();
    if (!object(result) || result.success !== true || !Array.isArray(result.results) || !object(result.meta)) throw new Error('result');
    // Never forward native error strings, SQL, binding identities or stack traces.
    const meta: Record<string, number | boolean> = {};
    for (const key of ['changes', 'size_after', 'rows_read', 'rows_written', 'duration', 'last_row_id', 'changed_db']) {
      const value = result.meta[key];
      if ((typeof value === 'number' && Number.isFinite(value)) || typeof value === 'boolean') meta[key] = value;
    }
    const response = JSON.stringify({ success: true, result: { success: true, results: result.results, meta } });
    if (encoder.encode(response).byteLength > RESPONSE_LIMIT) return error('archive_broker_response_limit', 502);
    return new Response(response, { headers: {
      'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
    } });
  } catch (cause) { return error(sanitizedError(cause), 503); }
}

export default { fetch: archiveBrokerFetch };
