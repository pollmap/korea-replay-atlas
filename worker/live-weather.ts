import {
  KMA_RADAR_PROJECTION, LIVE_RADAR_LOOKBACK_SECONDS, LIVE_RADAR_MAX_AGE_SECONDS,
  LIVE_WEATHER_API_PREFIX, LIVE_WEATHER_REFRESH_SECONDS,
  type LiveWeatherErrorCode, type LiveWeatherKind, type LiveWeatherManifest, type RawLiveFrame, type PanelLiveFrame,
  type RadarPanelLiveFrame, type SatellitePanelLiveFrame,
} from '../shared/live-weather';

const ORIGIN = 'https://www.weather.go.kr';
const LIST_PATH = '/w/wnuri-img/rest/radar/cmp/images.do';
const IMAGE_PATH = '/w/cgi-bin/rdr_new/nph-vs_rdr_cmp_img';
const LIST_MAX_BYTES = 64 * 1024, PNG_MAX_BYTES = 512 * 1024, METADATA_MAX_BYTES = 768 * 1024;
const SATELLITE_LIST_MAX_BYTES = 256 * 1024, SATELLITE_PNG_MAX_BYTES = 2 * 1024 * 1024;
const MAX_FRAMES = 13, IMAGE_TTL = 300, METADATA_TTL = 3600;
const KST_OFFSET = 9 * 3600 * 1000;
const OFFICIAL_RADAR_API = 'https://apis.data.go.kr/1360000/RadarImgInfoService/getCmpImg';
const OFFICIAL_RADAR_PERMISSION = 'https://www.data.go.kr/data/15056924/openapi.do';
const OFFICIAL_SATELLITE_API = 'https://apis.data.go.kr/1360000/SatlitImgInfoService/getInsightSatlit';
const OFFICIAL_SATELLITE_PERMISSION = 'https://www.data.go.kr/data/15058167/openapi.do';

export interface LiveWeatherCache {
  match(request: Request): Promise<Response | undefined>;
  put(request: Request, response: Response): Promise<void>;
}

export interface LiveWeatherOptions {
  fetcher?: typeof fetch;
  cache?: LiveWeatherCache;
  now?: () => number;
  waitUntil?: (promise: Promise<unknown>) => void;
  /** Enable only after the specific rendered product's reuse conditions are verified. */
  radarAccess?: 'verified' | 'unverified';
  /** A public policy/permission reference, never an authentication URL or secret. */
  permissionReference?: string;
  /** Each official service requires its own approval. Never expose this key. */
  dataGoKrServiceKey?: string;
  timeoutMs?: number;
}

class WeatherFailure extends Error {
  constructor(readonly code: LiveWeatherErrorCode, message: string, readonly status = 502, readonly retryable = true) { super(message); }
}

function formatKstStamp(milliseconds: number): string {
  return new Date(milliseconds + KST_OFFSET).toISOString().slice(0, 16).replace(/[-T:]/g, '');
}

function formatUtcStamp(milliseconds: number): string {
  return new Date(milliseconds).toISOString().slice(0, 16).replace(/[-T:]/g, '');
}

export function parseUtcSatelliteFrameTime(stamp: string): number {
  if (!/^20\d{10}$/.test(stamp)) throw new WeatherFailure('upstream_invalid', '위성 관측 시각 형식이 올바르지 않습니다.');
  const value = Date.UTC(+stamp.slice(0, 4), +stamp.slice(4, 6) - 1, +stamp.slice(6, 8), +stamp.slice(8, 10), +stamp.slice(10, 12));
  // GK2A filenames can end in :02/:07; a radar five-minute rule is not applicable.
  if (formatUtcStamp(value) !== stamp) throw new WeatherFailure('upstream_invalid', '위성 파일의 UTC 관측 시각이 유효하지 않습니다.');
  return value;
}

export function parseKstFrameTime(stamp: string): number {
  if (!/^20\d{10}$/.test(stamp)) throw new WeatherFailure('upstream_invalid', '레이더 관측 시각 형식이 올바르지 않습니다.');
  const value = Date.UTC(+stamp.slice(0, 4), +stamp.slice(4, 6) - 1, +stamp.slice(6, 8), +stamp.slice(8, 10), +stamp.slice(10, 12)) - KST_OFFSET;
  if (formatKstStamp(value) !== stamp || +stamp.slice(10, 12) % 5 !== 0) throw new WeatherFailure('upstream_invalid', '유효한 5분 단위 관측 시각이 아닙니다.');
  return value;
}

function sourceUrl(row: {tm: string; url: string}): string {
  if (row.url.length > 2048) throw new WeatherFailure('upstream_invalid', '레이더 영상 주소가 허용 길이를 초과했습니다.');
  let url: URL;
  try { url = new URL(row.url, ORIGIN); } catch { throw new WeatherFailure('upstream_invalid', '레이더 영상 주소가 올바르지 않습니다.'); }
  if (url.origin !== ORIGIN || url.pathname !== IMAGE_PATH || url.username || url.password || url.hash) throw new WeatherFailure('upstream_invalid', '허용되지 않은 레이더 원천 주소입니다.');
  const expected: Record<string, string> = {
    tm: row.tm, cmp: 'SFC', obs: 'HSR', qcd: 'PTY', acc: '', aws: '1', map: 'HR', color: 'C4',
    legend: '1', size: '640', zoom_level: '0', zoom_x: '0000000', zoom_y: '0000000', ZRa: '200', ZRb: '1.6',
    gis: '', rnexdisp: '0', griddisp: '0', disp: 'X',
  };
  for (const [key, value] of Object.entries(expected)) {
    if (url.searchParams.getAll(key).length !== 1 || url.searchParams.get(key) !== value) throw new WeatherFailure('upstream_invalid', '레이더 영상의 시각·제품·투영 매개변수가 변경되었습니다.');
  }
  for (const key of url.searchParams.keys()) {
    if (!Object.hasOwn(expected, key) && key !== 'rand') throw new WeatherFailure('upstream_invalid', '허용되지 않은 레이더 영상 매개변수입니다.');
  }
  const random = url.searchParams.getAll('rand');
  if (random.length > 1 || (random.length === 1 && !/^\d{1,10}$/.test(random[0]))) throw new WeatherFailure('upstream_invalid', '레이더 영상 요청 식별자가 올바르지 않습니다.');
  return url.href;
}

export function validateRadarProjectionMetadata(text: string): void {
  const match = /mapInfo\["radar\.extent"\]\s*=\s*(\[[^\]]+\])/.exec(text);
  let extent: unknown;
  try { extent = match ? JSON.parse(match[1]) : null; } catch { extent = null; }
  if (!Array.isArray(extent) || extent.length !== 4 || extent.some((value, index) => typeof value !== 'number' || !Number.isFinite(value) || Math.abs(value - KMA_RADAR_PROJECTION.extent[index]) > 0.01)
    || !text.includes(KMA_RADAR_PROJECTION.crs)) {
    throw new WeatherFailure('projection_changed', '기상청 레이더 투영 정의가 변경되어 좌표 검증이 필요합니다.', 503, false);
  }
}

let crcTable: Uint32Array | undefined;
function crc32(bytes: Uint8Array, start: number, end: number): number {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let value = 0; value < 256; value++) {
      let current = value;
      for (let bit = 0; bit < 8; bit++) current = (current >>> 1) ^ (current & 1 ? 0xedb88320 : 0);
      crcTable[value] = current;
    }
  }
  let result = 0xffffffff;
  for (let index = start; index < end; index++) result = crcTable[(result ^ bytes[index]) & 255] ^ (result >>> 8);
  return (result ^ 0xffffffff) >>> 0;
}

/** Bounded structural/CRC validation. Pixel decompression is done in the browser Worker. */
function validatePng(bytes: Uint8Array, width: number | null, height: number | null, maximum = PNG_MAX_BYTES): readonly [number, number] {
  const invalid = () => new WeatherFailure('upstream_invalid', '기상 원천 PNG의 형식·크기·무결성 검증에 실패했습니다.');
  if (bytes.length < 57 || bytes.length > maximum || ![137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => bytes[index] === value)) throw invalid();
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 8, chunks = 0, idat = false, idatEnded = false, palette = false, indexed = false;
  let actualWidth = 0, actualHeight = 0;
  while (offset < bytes.length) {
    if (++chunks > 128 || offset + 12 > bytes.length) throw invalid();
    const length = view.getUint32(offset), end = offset + length + 12;
    if (end > bytes.length) throw invalid();
    const type = String.fromCharCode(...bytes.subarray(offset + 4, offset + 8));
    if (!/^[A-Za-z]{2}[A-Z][A-Za-z]$/.test(type) || crc32(bytes, offset + 4, end - 4) !== view.getUint32(end - 4)) throw invalid();
    if (chunks === 1) {
      if (type !== 'IHDR' || length !== 13) throw invalid();
      actualWidth = view.getUint32(offset + 8); actualHeight = view.getUint32(offset + 12);
      if (actualWidth < 1 || actualHeight < 1 || actualWidth > 2048 || actualHeight > 2048
        || (width !== null && actualWidth !== width) || (height !== null && actualHeight !== height)) throw invalid();
      const depth = bytes[offset + 16], color = bytes[offset + 17];
      const depths: Record<number, number[]> = {0: [1, 2, 4, 8, 16], 2: [8, 16], 3: [1, 2, 4, 8], 4: [8, 16], 6: [8, 16]};
      if (!depths[color]?.includes(depth) || bytes[offset + 18] !== 0 || bytes[offset + 19] !== 0 || bytes[offset + 20] > 1) throw invalid();
      indexed = color === 3;
    } else if (type === 'IHDR' || ['acTL', 'fcTL', 'fdAT'].includes(type)) throw invalid();
    else if (type === 'PLTE') {
      if (idat || palette || length === 0 || length % 3 !== 0 || length > 768) throw invalid();
      palette = true;
    } else if (type === 'IDAT') {
      if (idatEnded || length === 0 || (indexed && !palette)) throw invalid();
      idat = true;
    } else if (type === 'IEND') {
      if (length !== 0 || !idat || end !== bytes.length) throw invalid();
      return [actualWidth, actualHeight];
    } else if (type[0] === type[0].toUpperCase()) throw invalid();
    if (idat && type !== 'IDAT') idatEnded = true;
    offset = end;
  }
  throw invalid();
}

export function validateRadarPng(bytes: Uint8Array): void {validatePng(bytes, 640, 640);}

interface Snapshot {checked_at: string; frames: RawLiveFrame[]}
interface OfficialSnapshot {checked_at: string; frames: PanelLiveFrame[]}

function apiKey(value: string | undefined): string | null {
  if (!value?.trim()) return null;
  try {
    const decoded = decodeURIComponent(value.trim());
    return /^[A-Za-z0-9+/_=-]{16,256}$/.test(decoded) ? decoded : null;
  } catch {return null;}
}

function officialFiles(value: unknown, field: 'rdr-img-file' | 'satImgC-file', maximum: number): unknown[] {
  if (!value || typeof value !== 'object') throw new WeatherFailure('upstream_invalid', '공식 기상 API의 응답 구조가 올바르지 않습니다.');
  const envelope = (value as {response?: {header?: {resultCode?: string}; body?: {items?: {item?: unknown}}}}).response;
  const code = envelope?.header?.resultCode;
  if (code !== '00') {
    if (code === '03') throw new WeatherFailure('frame_missing', '공식 기상 API에 해당 날짜의 관측 자료가 없습니다.', 503);
    if (code === '22' || code === '23') throw new WeatherFailure('upstream_quota', '공식 기상 API의 호출 허용량을 초과했습니다.', 503);
    if (['20', '30', '31', '32'].includes(code ?? '')) throw new WeatherFailure('authentication_failed', '공식 기상 API의 활용승인 또는 인증키 상태를 확인해야 합니다.', 503, false);
    throw new WeatherFailure('upstream_invalid', '공식 기상 API가 정상 자료를 반환하지 않았습니다.');
  }
  const items = envelope?.body?.items?.item;
  if (items === undefined || items === null || items === '') return [];
  const list = Array.isArray(items) ? items : [items];
  if (list.length > maximum) throw new WeatherFailure('upstream_invalid', '공식 기상 목록이 허용 범위를 초과했습니다.');
  const files: unknown[] = [];
  for (const item of list) {
    if (!item || typeof item !== 'object' || !Object.hasOwn(item, field)) throw new WeatherFailure('upstream_invalid', '공식 기상 파일 목록 필드가 없습니다.');
    let data = (item as Record<string, unknown>)[field];
    if (typeof data === 'string') {
      const text = data.trim();
      // The official JSON service also returns Java-style unquoted URL lists.
      if (text.startsWith('[') && text.endsWith(']')) {
        try {data = JSON.parse(text);} catch {data = text.slice(1, -1).split(',').map(url => url.trim());}
      } else data = text ? [text] : [];
    }
    if (!Array.isArray(data) || files.length + data.length > maximum) throw new WeatherFailure('upstream_invalid', '공식 기상 영상 목록 형식 또는 개수 한도가 변경되었습니다.');
    files.push(...data);
  }
  return files;
}

function parseOfficialRows(value: unknown, now: number): RadarPanelLiveFrame[] {
  const files = officialFiles(value, 'rdr-img-file', 300);
  const frames: RadarPanelLiveFrame[] = [], seen = new Set<string>();
  for (const file of files) {
    if (typeof file !== 'string' || file.length > 512) throw new WeatherFailure('upstream_invalid', '공식 레이더 영상 주소가 올바르지 않습니다.');
    let url: URL;
    try {url = new URL(file);} catch {throw new WeatherFailure('upstream_invalid', '공식 레이더 영상 주소가 올바르지 않습니다.');}
    const match = /^\/repositary\/image\/rdr\/img\/RDR_CMP_WRC_(20\d{10})\.png$/.exec(url.pathname);
    if (!['http:', 'https:'].includes(url.protocol) || !['www.kma.go.kr', 'www.weather.go.kr'].includes(url.hostname) || url.port || url.username || url.password || url.search || url.hash || !match) throw new WeatherFailure('upstream_invalid', '공식 레이더 API가 허용되지 않은 영상 주소를 반환했습니다.');
    url.protocol = 'https:';
    const stamp = match[1], time = parseKstFrameTime(stamp);
    if (seen.has(stamp)) throw new WeatherFailure('upstream_invalid', '공식 레이더 영상 시각이 중복되었습니다.');
    seen.add(stamp);
    if (time > now || now - time > LIVE_RADAR_LOOKBACK_SECONDS * 1000) continue;
    frames.push({kind: 'radar', product: 'CMP_WRC', representation: 'panel-image', map_overlay: false, projection: null,
      time: new Date(time).toISOString(), source_time_kst: stamp,
      url: `${LIVE_WEATHER_API_PREFIX}/radar/cmp-wrc/frames/${stamp}.png`, source_url: url.href, image_size: [635, 620]});
  }
  return frames.sort((left, right) => left.time.localeCompare(right.time)).slice(-MAX_FRAMES);
}

function parseSatelliteRows(value: unknown, now: number): SatellitePanelLiveFrame[] {
  const frames: SatellitePanelLiveFrame[] = [], seen = new Set<string>();
  for (const file of officialFiles(value, 'satImgC-file', 800)) {
    if (typeof file !== 'string' || file.length > 512) throw new WeatherFailure('upstream_invalid', '공식 위성 영상 주소가 올바르지 않습니다.');
    let url: URL;
    try {url = new URL(file);} catch {throw new WeatherFailure('upstream_invalid', '공식 위성 영상 주소가 올바르지 않습니다.');}
    const match = /^\/(?:w\/)?repositary\/image\/sat\/gk2a\/KO\/gk2a_ami_le1b_ir105_ko020lc_(20\d{10})(?:\.thn)?\.png$/.exec(url.pathname);
    if (!['http:', 'https:'].includes(url.protocol) || !['www.kma.go.kr', 'www.weather.go.kr'].includes(url.hostname)
      || url.port || url.username || url.password || url.search || url.hash || !match) throw new WeatherFailure('upstream_invalid', '공식 위성 API가 허용되지 않은 영상 주소를 반환했습니다.');
    // Preserve the exact filename variant returned by the API; never invent a thumbnail/full alias.
    url.protocol = 'https:';
    const stamp = match[1], time = parseUtcSatelliteFrameTime(stamp);
    if (seen.has(stamp)) throw new WeatherFailure('upstream_invalid', '공식 위성 영상 시각이 중복되었습니다.');
    seen.add(stamp);
    if (time > now || now - time > LIVE_RADAR_LOOKBACK_SECONDS * 1000) continue;
    frames.push({kind: 'satellite', product: 'GK2A_IR105_KO', representation: 'panel-image', map_overlay: false, projection: null,
      time: new Date(time).toISOString(), source_time_utc: stamp, source_time_kst: formatKstStamp(time),
      url: `${LIVE_WEATHER_API_PREFIX}/satellite/gk2a-ir105-ko/frames/${stamp}.png`, source_url: url.href, image_size: null});
  }
  return frames.sort((left, right) => left.time.localeCompare(right.time)).slice(-MAX_FRAMES);
}

function parseRows(value: unknown, now: number, requested: number): RawLiveFrame[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 30) throw new WeatherFailure('upstream_invalid', '유효한 레이더 프레임 목록이 없습니다.');
  const seen = new Set<string>(), frames: RawLiveFrame[] = [];
  for (const row of value) {
    if (!row || typeof row !== 'object' || typeof row.tm !== 'string' || typeof row.url !== 'string') throw new WeatherFailure('upstream_invalid', '레이더 목록의 관측 시각 또는 영상 주소가 없습니다.');
    const time = parseKstFrameTime(row.tm);
    const upstream = sourceUrl(row);
    if (seen.has(row.tm)) throw new WeatherFailure('upstream_invalid', '레이더 목록에 중복 관측 시각이 있습니다.');
    seen.add(row.tm);
    // The public endpoint sometimes includes a frame newer than the requested time.
    if (time > requested || now - time > LIVE_RADAR_LOOKBACK_SECONDS * 1000) continue;
    frames.push({kind: 'radar', product: 'SFC-HSR', time: new Date(time).toISOString(), source_time_kst: row.tm,
      url: `${LIVE_WEATHER_API_PREFIX}/radar/frames/${row.tm}.png`, source_url: upstream, projection: KMA_RADAR_PROJECTION});
  }
  return frames.sort((left, right) => left.time.localeCompare(right.time)).slice(-MAX_FRAMES);
}

function permittedReference(options: LiveWeatherOptions): string | null {
  if (options.radarAccess !== 'verified' || !options.permissionReference || options.permissionReference.length > 2048) return null;
  try {
    const url = new URL(options.permissionReference);
    if (url.protocol !== 'https:' || url.username || url.password || url.hash || !['kma.go.kr', 'weather.go.kr', 'data.go.kr'].some(host => url.hostname === host || url.hostname.endsWith(`.${host}`))) return null;
    if ([...url.searchParams.keys()].some(key => /key|token|password|secret|signature/i.test(key))) return null;
    return url.href;
  } catch { return null; }
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, {status, headers: {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}});
}

export function createLiveWeatherHandler(options: LiveWeatherOptions = {}) {
  const fetcher = options.fetcher ?? fetch, now = options.now ?? Date.now;
  const cache = options.cache ?? (typeof caches !== 'undefined' ? (caches as unknown as {default?: LiveWeatherCache}).default : undefined);
  const permission = permittedReference(options);
  const officialKey = apiKey(options.dataGoKrServiceKey);
  const timeout = Math.max(100, Math.min(options.timeoutMs ?? 8000, 15_000));

  async function fetchBytes(url: string, maximum: number, types: string[], signal: AbortSignal): Promise<Uint8Array> {
    const controller = new AbortController();
    let timedOut = false, reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    const abort = () => {controller.abort(signal.reason); void reader?.cancel().catch(() => undefined);};
    if (signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499, true);
    signal.addEventListener('abort', abort, {once: true});
    const timer = setTimeout(() => {timedOut = true; controller.abort(); void reader?.cancel().catch(() => undefined);}, timeout);
    try {
      const response = await fetcher(url, {method: 'GET', redirect: 'manual', signal: controller.signal,
        headers: {'Accept': types[0]}});
      if (controller.signal.aborted) throw new Error('aborted');
      if (response.status !== 200 || (response.url && response.url !== url)) {
        void response.body?.cancel().catch(() => undefined);
        if (response.status === 401 || response.status === 403) throw new WeatherFailure('authentication_failed', '원천 자료의 활용승인 또는 접근 상태를 확인해야 합니다.', 503, false);
        throw new WeatherFailure(response.status === 404 ? 'frame_missing' : 'upstream_http', '기상청 원천이 정상 자료를 반환하지 않았습니다.', response.status === 404 ? 404 : 502);
      }
      const type = response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase();
      const length = response.headers.get('Content-Length');
      if (!type || !types.includes(type) || (length !== null && (!/^\d+$/.test(length) || +length > maximum))) {
        void response.body?.cancel().catch(() => undefined);
        throw new WeatherFailure('upstream_invalid', '기상청 원천의 파일 형식 또는 크기가 허용 범위를 벗어났습니다.');
      }
      if (!response.body) throw new WeatherFailure('upstream_invalid', '기상청 원천 응답이 비어 있습니다.');
      reader = response.body.getReader();
      const chunks: Uint8Array[] = []; let size = 0;
      while (true) {
        const part = await reader.read();
        if (controller.signal.aborted) throw new Error('aborted');
        if (part.done) break;
        size += part.value.byteLength;
        if (size > maximum) {await reader.cancel(); throw new WeatherFailure('upstream_invalid', '기상청 원천 응답이 크기 상한을 초과했습니다.');}
        chunks.push(part.value);
      }
      const bytes = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
      return bytes;
    } catch (error) {
      if (signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      if (timedOut) throw new WeatherFailure('upstream_timeout', '기상청 원천 조회 시간이 초과되었습니다.', 504);
      if (error instanceof WeatherFailure) throw error;
      throw new WeatherFailure('upstream_unavailable', '기상청 원천에 연결할 수 없습니다.', 503);
    } finally {
      clearTimeout(timer); signal.removeEventListener('abort', abort); reader?.releaseLock();
    }
  }

  function cacheKey(origin: string, part: string): Request {return new Request(`${origin}/__live-weather-cache/v1/${part}`);}
  async function cached(origin: string, part: string, ttl: number): Promise<Response | undefined> {
    try {
      const response = await cache?.match(cacheKey(origin, part));
      if (!response) return;
      const stored = Number(response.headers.get('X-Live-Cached-At'));
      if (!Number.isFinite(stored) || stored > now() || now() - stored >= ttl * 1000) return;
      return response;
    } catch { return; }
  }
  async function store(origin: string, part: string, value: Response, ttl: number): Promise<void> {
    if (!cache) return;
    const headers = new Headers(value.headers);
    headers.set('Cache-Control', `public, max-age=${ttl}`); headers.set('X-Live-Cached-At', String(now()));
    const save = cache.put(cacheKey(origin, part), new Response(value.body, {status: value.status, headers})).catch(() => undefined);
    if (options.waitUntil) options.waitUntil(save); else await save;
  }
  async function projection(origin: string, signal: AbortSignal): Promise<void> {
    const hit = await cached(origin, 'radar/projection', METADATA_TTL);
    if (hit && await hit.text() === KMA_RADAR_PROJECTION.id) return;
    const bytes = await fetchBytes(KMA_RADAR_PROJECTION.metadata_url, METADATA_MAX_BYTES, ['application/javascript', 'text/javascript'], signal);
    validateRadarProjectionMetadata(new TextDecoder().decode(bytes));
    await store(origin, 'radar/projection', new Response(KMA_RADAR_PROJECTION.id), METADATA_TTL);
  }
  async function snapshot(origin: string, signal: AbortSignal): Promise<Snapshot> {
    const hit = await cached(origin, 'radar/list', LIVE_WEATHER_REFRESH_SECONDS);
    if (hit) return await hit.json() as Snapshot;
    await projection(origin, signal);
    const instant = now(), requested = Math.floor((instant - 5 * 60_000) / 300_000) * 300_000;
    const url = new URL(LIST_PATH, ORIGIN);
    url.search = new URLSearchParams({data: 'SFC-HSR', tm: formatKstStamp(requested), timeTerm: '5', leaflet: '1', wgis: '1', unit: 'm/s'}).toString();
    const bytes = await fetchBytes(url.href, LIST_MAX_BYTES, ['application/json'], signal);
    let value: unknown;
    try { value = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes)); }
    catch { throw new WeatherFailure('upstream_invalid', '기상청 레이더 목록이 올바른 JSON이 아닙니다.'); }
    const result: Snapshot = {checked_at: new Date(now()).toISOString(), frames: parseRows(value, now(), requested)};
    await store(origin, 'radar/list', json(result), LIVE_WEATHER_REFRESH_SECONDS);
    return result;
  }
  function manifest(kind: LiveWeatherKind, value?: Snapshot, failure?: WeatherFailure): LiveWeatherManifest {
    const current = now(), latest = value?.frames.at(-1)?.time ?? null;
    const stale = latest !== null && current - Date.parse(latest) > LIVE_RADAR_MAX_AGE_SECONDS * 1000;
    if (!failure && (!value?.frames.length || stale)) failure = new WeatherFailure(stale ? 'stale' : 'frame_missing', stale ? '최신 레이더 자료가 허용 지연 20분을 초과했습니다.' : '최근 2시간 안에 제공된 레이더 자료가 없습니다.', 503);
    return {
      schema_version: 1, mode: 'live', kind, status: stale ? 'stale' : failure ? 'unavailable' : 'available',
      checked_at: value?.checked_at ?? null, served_at: new Date(current).toISOString(), latest_observed_at: latest,
      max_age_seconds: LIVE_RADAR_MAX_AGE_SECONDS, refresh_after_seconds: LIVE_WEATHER_REFRESH_SECONDS,
      frames: failure || stale ? [] : value!.frames,
      presentation: failure || stale ? 'none' : 'map-overlay',
      source: {name: '기상청 날씨누리', page_url: `${ORIGIN}/w/weather/${kind === 'radar' ? 'radar/radar' : 'satellite/gk2a'}.do`,
        copyright_url: 'https://www.kma.go.kr/kma/guide/copyright.jsp', access: permission && kind === 'radar' ? 'verified' : 'unverified', permission_reference: kind === 'radar' ? permission : null},
      interpretation: {representation: 'official-color', numeric_inversion: false, background_meaning: 'no-echo-or-missing-unknown'},
      ...(failure ? {error: {code: failure.code, message: failure.message, retryable: failure.retryable}} : {}),
    };
  }

  function officialManifest(value?: OfficialSnapshot, failure?: WeatherFailure, kind: LiveWeatherKind = 'radar'): LiveWeatherManifest {
    const current = now(), latest = value?.frames.at(-1)?.time ?? null;
    const label = kind === 'radar' ? '레이더' : '위성', reference = kind === 'radar' ? OFFICIAL_RADAR_PERMISSION : OFFICIAL_SATELLITE_PERMISSION;
    const stale = latest !== null && current - Date.parse(latest) > LIVE_RADAR_MAX_AGE_SECONDS * 1000;
    if (!failure && (!value?.frames.length || stale)) failure = new WeatherFailure(stale ? 'stale' : 'frame_missing', stale ? `최신 공식 ${label} 자료가 허용 지연 20분을 초과했습니다.` : `최근 2시간 안의 공식 ${label} 자료가 없습니다.`, 503);
    return {
      schema_version: 1, mode: 'live', kind, status: stale ? 'stale' : failure ? 'unavailable' : 'available',
      checked_at: value?.checked_at ?? null, served_at: new Date(current).toISOString(), latest_observed_at: latest,
      max_age_seconds: LIVE_RADAR_MAX_AGE_SECONDS, refresh_after_seconds: LIVE_WEATHER_REFRESH_SECONDS,
      frames: [], panel_frames: failure || stale ? [] : value!.frames, presentation: failure || stale ? 'none' : 'panel-image',
      source: {name: `기상청 공공데이터 ${label}영상 API`, page_url: reference,
        copyright_url: 'https://www.kma.go.kr/kma/guide/copyright.jsp', access: value ? 'verified' : 'unverified', permission_reference: reference},
      interpretation: {representation: 'official-color', numeric_inversion: false, background_meaning: kind === 'radar' ? 'no-echo-or-missing-unknown' : 'source-rendering-not-classified'},
      ...(failure ? {error: {code: failure.code, message: failure.message, retryable: failure.retryable}} : {}),
    };
  }
  async function officialRadar(request: Request, url: URL, stamp?: string): Promise<Response> {
    let value: OfficialSnapshot | undefined;
    try {
      if (request.signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      if (!officialKey) throw new WeatherFailure('authentication_required', '공식 레이더 API 활용승인과 서버 인증키 연결이 필요합니다.', 503, false);
      if (stamp) {
        let time: number;
        try {time = parseKstFrameTime(stamp);} catch {throw new WeatherFailure('invalid_request', '레이더 프레임 시각이 올바르지 않습니다.', 400, false);}
        if (time > now() || now() - time > LIVE_RADAR_LOOKBACK_SECONDS * 1000) throw new WeatherFailure('frame_missing', '최근 제공 범위를 벗어난 공식 레이더 프레임입니다.', 404, false);
      }
      const hit = await cached(url.origin, 'radar/cmp-wrc/list', LIVE_WEATHER_REFRESH_SECONDS);
      if (hit) value = await hit.json() as OfficialSnapshot;
      else {
        const api = new URL(OFFICIAL_RADAR_API);
        api.search = new URLSearchParams({ServiceKey: officialKey, pageNo: '1', numOfRows: '10', dataType: 'JSON', data: 'CMP_WRC', time: formatKstStamp(now() - 5 * 60_000).slice(0, 8)}).toString();
        const bytes = await fetchBytes(api.href, LIST_MAX_BYTES, ['application/json'], request.signal);
        let response: unknown;
        try {response = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));} catch {throw new WeatherFailure('upstream_invalid', '공식 레이더 API가 올바른 JSON을 반환하지 않았습니다.');}
        value = {checked_at: new Date(now()).toISOString(), frames: parseOfficialRows(response, now())};
        await store(url.origin, 'radar/cmp-wrc/list', json(value), LIVE_WEATHER_REFRESH_SECONDS);
      }
      if (request.signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      const result = officialManifest(value);
      if (result.status !== 'available') return json(result, 503);
      if (!stamp) return json(result);
      const frame = value.frames.find(frame => frame.source_time_kst === stamp);
      if (!frame) throw new WeatherFailure('frame_missing', '공식 API 목록에 없는 레이더 프레임입니다.', 404, false);
      const part = `radar/cmp-wrc/frames/${stamp}`;
      let image = await cached(url.origin, part, IMAGE_TTL);
      if (!image) {
        const bytes = await fetchBytes(frame.source_url, PNG_MAX_BYTES, ['image/png'], request.signal);
        validatePng(bytes, 635, 620);
        image = new Response(bytes as BodyInit, {headers: {'Content-Type': 'image/png', 'Cache-Control': `public, max-age=${IMAGE_TTL}`,
          'X-Content-Type-Options': 'nosniff', 'X-Weather-Observed-At': frame.time, 'X-Weather-Representation': 'panel-image', 'X-Weather-Map-Overlay': 'false'}});
        await store(url.origin, part, image.clone(), IMAGE_TTL);
      }
      if (request.signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      return image;
    } catch (error) {
      const failure = error instanceof WeatherFailure ? error : new WeatherFailure('upstream_invalid', '공식 레이더 자료 계약 검증에 실패했습니다.');
      return json(officialManifest(value, failure), failure.status);
    }
  }

  async function officialSatellite(request: Request, url: URL, stamp?: string): Promise<Response> {
    let value: OfficialSnapshot | undefined;
    try {
      if (request.signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      if (!officialKey) throw new WeatherFailure('authentication_required', '공식 위성 API 활용승인과 서버 인증키 연결이 필요합니다.', 503, false);
      if (stamp) {
        let time: number;
        try {time = parseUtcSatelliteFrameTime(stamp);} catch {throw new WeatherFailure('invalid_request', '위성 프레임의 UTC 시각이 올바르지 않습니다.', 400, false);}
        if (time > now() || now() - time > LIVE_RADAR_LOOKBACK_SECONDS * 1000) throw new WeatherFailure('frame_missing', '최근 제공 범위를 벗어난 공식 위성 프레임입니다.', 404, false);
      }
      const part = 'satellite/gk2a-ir105-ko', hit = await cached(url.origin, `${part}/list`, LIVE_WEATHER_REFRESH_SECONDS);
      if (hit) value = await hit.json() as OfficialSnapshot;
      else {
        const instant = now(), dates = new Set<string>();
        // This product groups the source files by UTC date. Confirmed against both
        // dates after KST midnight: the future UTC day returned 01, the current UTC
        // day returned today's actual frames. Never probe a future UTC date.
        for (const time of [instant, instant - LIVE_RADAR_LOOKBACK_SECONDS * 1000]) {
          dates.add(formatUtcStamp(time).slice(0, 8));
        }
        const frames = new Map<string, SatellitePanelLiveFrame>();
        for (const date of [...dates].sort().reverse()) {
          const api = new URL(OFFICIAL_SATELLITE_API);
          api.search = new URLSearchParams({ServiceKey: officialKey, pageNo: '1', numOfRows: '10', dataType: 'JSON', sat: 'G2', data: 'ir105', area: 'ko', time: date}).toString();
          const bytes = await fetchBytes(api.href, SATELLITE_LIST_MAX_BYTES, ['application/json'], request.signal);
          let response: unknown;
          try {response = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));} catch {throw new WeatherFailure('upstream_invalid', '공식 위성 API가 올바른 JSON을 반환하지 않았습니다.');}
          let rows: SatellitePanelLiveFrame[];
          try {rows = parseSatelliteRows(response, now());}
          catch (error) {
            if (error instanceof WeatherFailure && error.code === 'frame_missing' && error.status === 503) continue;
            // In particular, an authentication failure must never trigger another date request.
            throw error;
          }
          for (const frame of rows) {
            const existing = frames.get(frame.source_time_utc);
            if (existing && existing.source_url !== frame.source_url) throw new WeatherFailure('upstream_invalid', '동일 위성 관측 시각의 원천 영상 주소가 서로 다릅니다.');
            frames.set(frame.source_time_utc, frame);
          }
        }
        value = {checked_at: new Date(now()).toISOString(), frames: [...frames.values()].sort((left, right) => left.time.localeCompare(right.time)).slice(-MAX_FRAMES)};
        await store(url.origin, `${part}/list`, json(value), LIVE_WEATHER_REFRESH_SECONDS);
      }
      if (request.signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      const result = officialManifest(value, undefined, 'satellite');
      if (result.status !== 'available') return json(result, 503);
      if (!stamp) return json(result);
      const frame = value.frames.find((frame): frame is SatellitePanelLiveFrame => frame.kind === 'satellite' && frame.source_time_utc === stamp);
      if (!frame) throw new WeatherFailure('frame_missing', '공식 API 목록에 없는 위성 프레임입니다.', 404, false);
      const framePart = `${part}/frames/${stamp}`;
      let image = await cached(url.origin, framePart, IMAGE_TTL);
      if (!image) {
        const bytes = await fetchBytes(frame.source_url, SATELLITE_PNG_MAX_BYTES, ['image/png'], request.signal);
        const size = validatePng(bytes, null, null, SATELLITE_PNG_MAX_BYTES);
        image = new Response(bytes as BodyInit, {headers: {'Content-Type': 'image/png', 'Cache-Control': `public, max-age=${IMAGE_TTL}`,
          'X-Content-Type-Options': 'nosniff', 'X-Weather-Observed-At': frame.time, 'X-Weather-Representation': 'panel-image', 'X-Weather-Map-Overlay': 'false',
          'X-Weather-Image-Width': String(size[0]), 'X-Weather-Image-Height': String(size[1])}});
        await store(url.origin, framePart, image.clone(), IMAGE_TTL);
      }
      if (request.signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      return image;
    } catch (error) {
      const failure = error instanceof WeatherFailure ? error : new WeatherFailure('upstream_invalid', '공식 위성 자료 계약 검증에 실패했습니다.');
      return json(officialManifest(value, failure, 'satellite'), failure.status);
    }
  }

  return async function handleLiveWeather(request: Request): Promise<Response | null> {
    const url = new URL(request.url);
    if (!url.pathname.startsWith(`${LIVE_WEATHER_API_PREFIX}/`)) return null;
    const officialRoute = /^\/radar\/cmp-wrc\/frames\/(\d{12})\.png$/.exec(url.pathname.slice(LIVE_WEATHER_API_PREFIX.length));
    if (officialRoute && !url.search && request.method === 'GET') return officialRadar(request, url, officialRoute[1]);
    const satelliteRoute = /^\/satellite\/gk2a-ir105-ko\/frames\/(\d{12})\.png$/.exec(url.pathname.slice(LIVE_WEATHER_API_PREFIX.length));
    if (satelliteRoute && !url.search && request.method === 'GET') return officialSatellite(request, url, satelliteRoute[1]);
    const route = /^\/(radar|satellite)(?:\/frames\/(\d{12})\.png)?$/.exec(url.pathname.slice(LIVE_WEATHER_API_PREFIX.length));
    if (!route || url.search || request.method !== 'GET') return json({error: {code: 'invalid_request', message: '허용된 실시간 기상 GET 경로를 사용해 주세요.', retryable: false}}, request.method !== 'GET' ? 405 : 400);
    const kind = route[1] as LiveWeatherKind;
    if (kind === 'radar' && !route[2] && options.dataGoKrServiceKey) return officialRadar(request, url);
    if (kind === 'satellite' && !route[2]) return officialSatellite(request, url);
    let value: Snapshot | undefined;
    try {
      if (request.signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      if (kind === 'satellite') throw new WeatherFailure('projection_unverified', '실시간 위성 지도 영상은 투영·이용 조건 검증이 완료되지 않았습니다.', 503, false);
      if (!permission) throw new WeatherFailure('source_not_verified', '공개 레이더 영상의 운영 재이용 조건이 아직 연결되지 않았습니다.', 503, false);
      if (route[2]) {
        let time: number;
        try {time = parseKstFrameTime(route[2]);} catch {throw new WeatherFailure('invalid_request', '레이더 프레임 시각이 올바르지 않습니다.', 400, false);}
        if (time > now() || now() - time > LIVE_RADAR_LOOKBACK_SECONDS * 1000) throw new WeatherFailure('frame_missing', '최근 제공 범위를 벗어난 레이더 프레임입니다.', 404, false);
      }
      value = await snapshot(url.origin, request.signal);
      if (request.signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      const result = manifest(kind, value);
      if (result.status !== 'available') return json(result, 503);
      if (!route[2]) return json(result);
      const frame = value.frames.find(frame => frame.source_time_kst === route[2]);
      if (!frame) throw new WeatherFailure('frame_missing', '검증된 최신 목록에 없는 레이더 프레임입니다.', 404, false);
      const part = `radar/frames/${frame.source_time_kst}`;
      let image = await cached(url.origin, part, IMAGE_TTL);
      if (!image) {
        const bytes = await fetchBytes(frame.source_url, PNG_MAX_BYTES, ['image/png'], request.signal);
        validateRadarPng(bytes);
        image = new Response(bytes as BodyInit, {headers: {'Content-Type': 'image/png', 'Cache-Control': `public, max-age=${IMAGE_TTL}`,
          'X-Content-Type-Options': 'nosniff', 'X-Weather-Observed-At': frame.time, 'X-Weather-Projection': KMA_RADAR_PROJECTION.id}});
        await store(url.origin, part, image.clone(), IMAGE_TTL);
      }
      if (request.signal.aborted) throw new WeatherFailure('aborted', '기상 자료 조회가 취소되었습니다.', 499);
      return image;
    } catch (error) {
      const failure = error instanceof WeatherFailure ? error : new WeatherFailure('upstream_invalid', '기상 자료 계약 검증에 실패했습니다.');
      return json(manifest(kind, value, failure), failure.status);
    }
  };
}
