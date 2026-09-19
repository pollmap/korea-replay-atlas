import {describe, expect, it, vi} from 'vitest';
import {deflateSync} from 'node:zlib';
import {createLiveWeatherHandler, parseKstFrameTime, parseUtcSatelliteFrameTime, validateRadarPng, validateRadarProjectionMetadata, type LiveWeatherCache} from '../worker/live-weather';
import {isLiveWeatherFresh, KMA_RADAR_PROJECTION, LIVE_WEATHER_API_PREFIX, type LiveWeatherManifest} from '../shared/live-weather';

const BASE = LIVE_WEATHER_API_PREFIX, INSTANT = Date.parse('2026-09-16T16:12:00.000Z');
const PERMISSION = 'https://www.kma.go.kr/kma/guide/copyright.jsp'; // Fixture only, not a deployed product permission claim.
const METADATA = `mapInfo["radar.extent"]=${JSON.stringify(KMA_RADAR_PROJECTION.extent)}; mapInfo["EPSG:980201"]="${KMA_RADAR_PROJECTION.crs}";`;

function imageUrl(tm: string): string {
  const params = new URLSearchParams({tm, cmp: 'SFC', obs: 'HSR', qcd: 'PTY', acc: '', aws: '1', map: 'HR', color: 'C4',
    legend: '1', size: '640', zoom_level: '0', zoom_x: '0000000', zoom_y: '0000000', ZRa: '200', ZRb: '1.6', rand: '12160', gis: '', rnexdisp: '0', griddisp: '0', disp: 'X'});
  return `/w/cgi-bin/rdr_new/nph-vs_rdr_cmp_img?${params}`;
}
const row = (tm = '202609170105') => ({tm, url: imageUrl(tm), name: 'A display label is never the observation timestamp'});

function pngChunk(type: string, data: Uint8Array): Buffer {
  const head = Buffer.from(type), payload = Buffer.concat([head, data]);
  let crc = 0xffffffff;
  for (const byte of payload) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  const output = Buffer.alloc(data.length + 12);
  output.writeUInt32BE(data.length); payload.copy(output, 4); output.writeUInt32BE((crc ^ 0xffffffff) >>> 0, output.length - 4);
  return output;
}
function png(width = 640, height = 640, extra?: Buffer): Buffer {
  const header = Buffer.alloc(13); header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), pngChunk('IHDR', header),
    ...(extra ? [extra] : []), pngChunk('IDAT', deflateSync(Buffer.alloc((width * 4 + 1) * height))), pngChunk('IEND', Buffer.alloc(0))]);
}
const VALID_PNG = png();
const pngResponse = (bytes: Uint8Array, init: ResponseInit) => new Response(Uint8Array.from(bytes).buffer, init);

function fixture(options: {rows?: unknown; metadata?: string; image?: () => Response; access?: 'verified' | 'unverified'; reference?: string} = {}) {
  let current = INSTANT;
  const responses = new Map<string, Response>();
  const cache: LiveWeatherCache = {match: vi.fn(async key => responses.get(key.url)?.clone()), put: vi.fn(async (key, value) => {responses.set(key.url, value.clone());})};
  const requests: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : input.toString()); requests.push(url.href);
    expect(init?.redirect).toBe('manual'); expect(init?.method).toBe('GET');
    if (url.href === KMA_RADAR_PROJECTION.metadata_url) return new Response(options.metadata ?? METADATA, {headers: {'Content-Type': 'application/javascript'}});
    if (url.pathname.endsWith('/images.do')) return Response.json(options.rows ?? [row('202609170055'), row('202609170100'), row(), row('202609170110')]);
    if (url.pathname.endsWith('/nph-vs_rdr_cmp_img')) return options.image?.() ?? pngResponse(VALID_PNG, {headers: {'Content-Type': 'image/png'}});
    throw new Error('Unexpected fixture request');
  }) as typeof fetch;
  const handler = createLiveWeatherHandler({fetcher, cache, now: () => current, radarAccess: options.access ?? 'verified', permissionReference: options.reference ?? PERMISSION});
  const call = async (path = `${BASE}/radar`, init?: RequestInit) => (await handler(new Request(`https://app.example${path}`, init)))!;
  return {call, handler, fetcher, cache, requests, responses, advance: (milliseconds: number) => {current += milliseconds;}};
}

describe('live weather contracts and deployment boundary', () => {
  it('requires specific permission configuration before making any upstream request', async () => {
    const f = fixture({access: 'unverified'}), response = await f.call(), body = await response.json() as LiveWeatherManifest;
    expect(response.status).toBe(503); expect(body).toMatchObject({status: 'unavailable', checked_at: null, frames: [], error: {code: 'source_not_verified'}});
    expect(f.fetcher).not.toHaveBeenCalled();
  });
  it.each(['https://evil.example/policy', 'https://www.kma.go.kr/policy?authKey=fixture-secret', 'https://kma.go.kr.evil.example/policy', 'https://user:password@www.kma.go.kr/policy'])('rejects a nonpublic/foreign permission reference %s', async reference => {
    const f = fixture({reference}), body = await (await f.call()).json() as LiveWeatherManifest;
    expect(body.source.access).toBe('unverified'); expect(body.source.permission_reference).toBeNull(); expect(f.fetcher).not.toHaveBeenCalled();
  });
  it('requires separate satellite API credentials and never enables an unverified map overlay', async () => {
    const f = fixture(), response = await f.call(`${BASE}/satellite`), body = await response.json() as LiveWeatherManifest;
    expect(response.status).toBe(503); expect(body).toMatchObject({kind: 'satellite', status: 'unavailable', latest_observed_at: null, frames: [], panel_frames: [], error: {code: 'authentication_required'}});
    expect(await (await f.call(`${BASE}/satellite/frames/202609170105.png`)).json()).toMatchObject({error: {code: 'projection_unverified'}, frames: []});
    expect(f.fetcher).not.toHaveBeenCalled();
  });
  it('routes only exact GET endpoints and does not accept arbitrary upstream URLs', async () => {
    const f = fixture();
    expect(await f.handler(new Request('https://app.example/api/v1/catalog'))).toBeNull();
    for (const path of [`${BASE}/radar?url=https://evil.example`, `${BASE}/radar/extra`, `${BASE}/radar/frames/../../x`]) expect((await f.call(path)).status).toBe(400);
    expect((await f.call(`${BASE}/radar`, {method: 'POST'})).status).toBe(405); expect(f.fetcher).not.toHaveBeenCalled();
  });
});

const satelliteFile = (stamp = '202609161602') => `https://www.weather.go.kr/w/repositary/image/sat/gk2a/KO/gk2a_ami_le1b_ir105_ko020lc_${stamp}.thn.png`;
const SATELLITE_PNG = png(600, 614);
const satelliteFramePath = (stamp = '202609161602') => `${BASE}/satellite/gk2a-ir105-ko/frames/${stamp}.png`;

function satelliteFixture(options: {files?: unknown; code?: string; status?: number; image?: () => Response; key?: string; instant?: number; byDate?: (date: string) => {files?: unknown; code?: string}} = {}) {
  let current = options.instant ?? INSTANT;
  const requests: URL[] = [], responses = new Map<string, Response>();
  const cache: LiveWeatherCache = {match: async key => responses.get(key.url)?.clone(), put: async (key, value) => {responses.set(key.url, value.clone());}};
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : input.toString()); requests.push(url);
    expect(init?.redirect).toBe('manual');
    if (url.pathname === '/1360000/SatlitImgInfoService/getInsightSatlit') {
      if (options.status) return new Response('Provider error body is never exposed.', {status: options.status});
      const date = options.byDate?.(url.searchParams.get('time')!);
      return Response.json({response: {header: {resultCode: date?.code ?? options.code ?? '00'}, body: {items: {item: [{'satImgC-file': date?.files ?? options.files ?? [satelliteFile()]}]}}}});
    }
    if (url.pathname.includes('/image/sat/gk2a/')) return options.image?.() ?? pngResponse(SATELLITE_PNG, {headers: {'Content-Type': 'image/png'}});
    throw new Error('Unexpected fixture request');
  }) as typeof fetch;
  const handler = createLiveWeatherHandler({fetcher, cache, now: () => current, dataGoKrServiceKey: options.key ?? 'fixture_service_key_1234567890'});
  const call = async (path = `${BASE}/satellite`, init?: RequestInit) => (await handler(new Request(`https://app.example${path}`, init)))!;
  return {call, requests, responses, advance: (milliseconds: number) => {current += milliseconds;}};
}

describe('official satellite panel and UTC observation contract', () => {
  it.each([[satelliteFile()], JSON.stringify([satelliteFile()]), `[${satelliteFile()}]`])('uses filename UTC without applying the radar five-minute rule', async files => {
    const f = satelliteFixture({files}), response = await f.call(), text = await response.text(), body = JSON.parse(text) as LiveWeatherManifest;
    expect(response.status).toBe(200);
    expect(body).toMatchObject({kind: 'satellite', status: 'available', presentation: 'panel-image', frames: [], interpretation: {background_meaning: 'source-rendering-not-classified'}});
    expect(body.panel_frames).toEqual([{kind: 'satellite', product: 'GK2A_IR105_KO', representation: 'panel-image', map_overlay: false, projection: null,
      time: '2026-09-16T16:02:00.000Z', source_time_utc: '202609161602', source_time_kst: '202609170102', image_size: null, url: satelliteFramePath(), source_url: satelliteFile()}]);
    expect(isLiveWeatherFresh(body, INSTANT)).toBe(true); expect(isLiveWeatherFresh(body, INSTANT + 11 * 60_000)).toBe(false);
    expect(f.requests.map(url => url.searchParams.get('time'))).toEqual(['20260916']);
    for (const request of f.requests) {
      expect(request.searchParams.get('sat')).toBe('G2'); expect(request.searchParams.get('data')).toBe('ir105'); expect(request.searchParams.get('area')).toBe('ko');
    }
    expect(text).not.toContain('fixture_service_key'); expect([...f.responses.keys()].join(' ')).not.toContain('fixture_service_key');
  });
  it('validates calendar dates independently of display timezone', () => {
    expect(parseUtcSatelliteFrameTime('202402292307')).toBe(Date.parse('2024-02-29T23:07:00Z'));
    for (const stamp of ['202602291602', '202613011602', '202609162402', '202609161660', '2026091616']) expect(() => parseUtcSatelliteFrameTime(stamp)).toThrow();
  });
  it('uses the previous UTC day across UTC midnight without relabeling its observation', async () => {
    const f = satelliteFixture({instant: Date.parse('2026-09-17T00:07:00Z'), byDate: date => date === '20260917' ? {code: '03'} : {files: [satelliteFile('202609162357')]}});
    expect(await (await f.call()).json()).toMatchObject({status: 'available', latest_observed_at: '2026-09-16T23:57:00.000Z'});
    expect(f.requests.map(url => url.searchParams.get('time'))).toEqual(['20260917', '20260916']);
    expect(f.requests).toHaveLength(2);
  });
  it('does not query a future UTC date after KST midnight, even if that date would return application error 01', async () => {
    const f = satelliteFixture({instant: Date.parse('2026-09-19T15:57:00Z'), byDate: date => date === '20260920' ? {code: '01'} : {files: [satelliteFile('202609191552')]}});
    const response=await f.call();expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({status:'available',latest_observed_at:'2026-09-19T15:52:00.000Z'});
    expect(f.requests.map(url=>url.searchParams.get('time'))).toEqual(['20260919']);
  });
  it('merges both UTC dates at the year boundary and retains actual UTC timestamps', async () => {
    const f = satelliteFixture({instant:Date.parse('2027-01-01T00:07:00Z'),byDate:date=>({files:[satelliteFile(date==='20270101'?'202701010002':'202612312357')]})});
    const body=await (await f.call()).json() as LiveWeatherManifest;
    expect(body.panel_frames?.map(frame=>frame.time)).toEqual(['2026-12-31T23:57:00.000Z','2027-01-01T00:02:00.000Z']);
    expect(f.requests.map(url=>url.searchParams.get('time'))).toEqual(['20270101','20261231']);
  });
  it('does not reinterpret a current UTC-day application error as empty data or bypass it with another date',async()=>{
    const f=satelliteFixture({instant:Date.parse('2026-09-20T00:07:00Z'),code:'01'});
    const response=await f.call();expect(response.status).toBe(502);
    expect(await response.json()).toMatchObject({status:'unavailable',error:{code:'upstream_invalid'},panel_frames:[]});
    expect(f.requests).toHaveLength(1);
  });
  it.each([{status: 403}, {code: '20'}, {code: '30'}])('stops on denied access without another date or public-source fallback: %j', async options => {
    const f = satelliteFixture(options), response = await f.call(), text = await response.text();
    expect(response.status).toBe(503); expect(JSON.parse(text)).toMatchObject({status: 'unavailable', checked_at: null, panel_frames: [], frames: [], source: {access: 'unverified'}, error: {code: 'authentication_failed'}});
    expect(f.requests).toHaveLength(1); expect(f.responses.size).toBe(0); expect(text).not.toContain('Provider error'); expect(text).not.toContain('fixture_service_key');
  });
  it('delivers bounded original PNG bytes with explicit panel-only headers and separate cache TTLs', async () => {
    const f = satelliteFixture(), initial = await (await f.call()).json() as LiveWeatherManifest;
    f.advance(30_000); const later = await (await f.call()).json() as LiveWeatherManifest;
    expect(later.checked_at).toBe(initial.checked_at); expect(later.served_at).not.toBe(initial.served_at);
    const image = await f.call(satelliteFramePath());
    expect(image.status).toBe(200); expect(image.headers.get('X-Weather-Observed-At')).toBe('2026-09-16T16:02:00.000Z');
    expect(image.headers.get('X-Weather-Map-Overlay')).toBe('false'); expect(image.headers.get('X-Weather-Image-Width')).toBe('600'); expect(image.headers.get('X-Weather-Image-Height')).toBe('614');
    expect(Buffer.from(await image.arrayBuffer())).toEqual(SATELLITE_PNG); await f.call(satelliteFramePath()); expect(f.requests).toHaveLength(2);
    f.advance(31_000); await f.call(satelliteFramePath()); expect(f.requests).toHaveLength(3);
    expect(f.requests.filter(url => url.pathname.includes('/image/sat/'))).toHaveLength(1);
  });
  it.each([
    satelliteFile().replace('www.weather.go.kr', 'evil.example'), satelliteFile().replace('ir105', 'vi006'), satelliteFile().replace('ko020lc', 'ea020lc'),
    `${satelliteFile()}?ServiceKey=hidden`, satelliteFile().replace('https://', 'https://user:password@'), satelliteFile().replace('202609161602', '202609311602'),
  ])('rejects another host/product/area/auth query/date: %s', async file => {
    const f = satelliteFixture({files: [file]}), response = await f.call(), text = await response.text();
    expect(response.status).toBe(502); expect(JSON.parse(text)).toMatchObject({error: {code: 'upstream_invalid'}, panel_frames: []}); expect(text).not.toContain(file); expect(f.requests).toHaveLength(1);
  });
  it('keeps a delayed observation stale and excludes future/old files', async () => {
    const f = satelliteFixture({files: [satelliteFile('202609161530'), satelliteFile('202609161617'), satelliteFile('202609161300')]}), body = await (await f.call()).json();
    expect(body).toMatchObject({status: 'stale', latest_observed_at: '2026-09-16T15:30:00.000Z', panel_frames: [], error: {code: 'stale'}});
  });
  it.each([
    () => new Response('<html>not an image</html>', {headers: {'Content-Type': 'text/html'}}),
    () => pngResponse(png(2049, 1), {headers: {'Content-Type': 'image/png'}}),
    () => pngResponse(SATELLITE_PNG, {headers: {'Content-Type': 'image/png', 'Content-Length': String(2 * 1024 * 1024 + 1)}}),
    () => {const broken = Buffer.from(SATELLITE_PNG); broken[40] ^= 1; return pngResponse(broken, {headers: {'Content-Type': 'image/png'}});},
  ])('rejects HTML, oversized dimensions/bytes, or corrupt PNG without caching the image', async image => {
    const f = satelliteFixture({image}), response = await f.call(satelliteFramePath());
    expect(response.status).toBe(502); expect(await response.json()).toMatchObject({error: {code: 'upstream_invalid'}, panel_frames: []});
    expect([...f.responses.keys()].some(key => key.includes('/frames/'))).toBe(false);
  });
  it('requires a listed UTC frame and rejects invalid routes without fetching arbitrary images', async () => {
    const f = satelliteFixture();
    expect((await f.call(satelliteFramePath('202609161603'))).status).toBe(404);
    expect((await f.call(satelliteFramePath('202609311602'))).status).toBe(400);
    expect((await f.call(`${satelliteFramePath()}?url=https://evil.example`)).status).toBe(400);
    expect((await f.call(satelliteFramePath(), {method: 'POST'})).status).toBe(405);
    expect(f.requests).toHaveLength(1);
  });
  it('does not fetch with missing credentials or a pre-aborted request', async () => {
    const absent = satelliteFixture({key: ''});
    expect(await (await absent.call()).json()).toMatchObject({error: {code: 'authentication_required'}}); expect(absent.requests).toHaveLength(0);
    const f = satelliteFixture(), controller = new AbortController(); controller.abort();
    expect((await f.call(`${BASE}/satellite`, {signal: controller.signal})).status).toBe(499); expect(f.requests).toHaveLength(0);
  });
});

describe('live radar list and source projection', () => {
  it('returns UTC observation time, independent checked/served time, and excludes newer-than-requested frames', async () => {
    const f = fixture(), response = await f.call(), body = await response.json() as LiveWeatherManifest;
    expect(response.status).toBe(200); expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(body).toMatchObject({mode: 'live', status: 'available', checked_at: '2026-09-16T16:12:00.000Z', latest_observed_at: '2026-09-16T16:05:00.000Z', max_age_seconds: 1200});
    expect(body.frames.map(frame => frame.source_time_kst)).toEqual(['202609170055', '202609170100', '202609170105']);
    expect(body.frames[2]).toMatchObject({time: '2026-09-16T16:05:00.000Z', url: `${BASE}/radar/frames/202609170105.png`, projection: KMA_RADAR_PROJECTION});
    expect(f.requests).toHaveLength(2); expect(new URL(f.requests[1]).searchParams.get('tm')).toBe('202609170105');
    expect(isLiveWeatherFresh(body, INSTANT)).toBe(true); expect(isLiveWeatherFresh(body, INSTANT + 14 * 60_000)).toBe(false);
    expect(isLiveWeatherFresh({...body, max_age_seconds: Infinity}, INSTANT)).toBe(false);
    expect(isLiveWeatherFresh({...body, frames: []}, INSTANT)).toBe(false);
  });
  it('preserves source check time on a cache hit and refreshes only the short-lived list', async () => {
    const f = fixture(), first = await (await f.call()).json() as LiveWeatherManifest;
    f.advance(30_000); const second = await (await f.call()).json() as LiveWeatherManifest;
    expect(second.checked_at).toBe(first.checked_at); expect(second.served_at).not.toBe(first.served_at); expect(f.requests).toHaveLength(2);
    f.advance(31_000); const third = await (await f.call()).json() as LiveWeatherManifest;
    expect(third.checked_at).not.toBe(first.checked_at); expect(f.requests).toHaveLength(3);
    expect(f.requests.filter(url => url === KMA_RADAR_PROJECTION.metadata_url)).toHaveLength(1);
  });
  it('fails closed on changed official projection and does not fetch or cache a frame list', async () => {
    const f = fixture({metadata: METADATA.replace('-440000', '-440100')}), response = await f.call();
    expect(response.status).toBe(503); expect(await response.json()).toMatchObject({status: 'unavailable', error: {code: 'projection_changed'}, frames: []});
    expect(f.requests).toHaveLength(1); expect(f.cache.put).not.toHaveBeenCalled();
    expect(() => validateRadarProjectionMetadata(METADATA)).not.toThrow();
    expect(() => validateRadarProjectionMetadata(METADATA.replace('+lon_0=126', '+lon_0=127'))).toThrow();
  });
  it('does not portray a delayed observation or an absent recent frame as live', async () => {
    const f = fixture({rows: [row('202609170040')]}), response = await f.call();
    expect(response.status).toBe(503); expect(await response.json()).toMatchObject({status: 'stale', latest_observed_at: '2026-09-16T15:40:00.000Z', frames: [], error: {code: 'stale'}});
    const absent = fixture({rows: [row('202609161900')]});
    expect(await (await absent.call()).json()).toMatchObject({status: 'unavailable', frames: [], error: {code: 'frame_missing'}});
  });
  it('bounds list size and reports duplicate/invalid dates as errors', async () => {
    for (const rows of [[], Array.from({length: 31}, () => row()), [row(), row()], [row('202609170103')], [row('202602300100')], [{tm: 202609170100, url: imageUrl('202609170100')}]]) {
      const f = fixture({rows}), response = await f.call();
      expect(response.status).toBe(502); expect(await response.json()).toMatchObject({error: {code: 'upstream_invalid'}, frames: []});
    }
    expect(parseKstFrameTime('202609170105')).toBe(Date.parse('2026-09-16T16:05:00Z'));
  });
  it.each([
    (url: string) => `https://evil.example${url}`,
    (url: string) => url.replace('obs=HSR', 'obs=PPI'),
    (url: string) => url.replace('size=640', 'size=1024'),
    (url: string) => url.replace('tm=202609170105', 'tm=202609170100'),
    (url: string) => `${url}&obs=HSR`,
    (url: string) => `${url}&constructor=untrusted`,
    (url: string) => `${url}&authKey=untrusted`,
  ])('rejects a changed origin/product/query contract without requesting that image', async change => {
    const f = fixture({rows: [{...row(), url: change(row().url)}]}), response = await f.call();
    expect(response.status).toBe(502); expect(await response.json()).toMatchObject({error: {code: 'upstream_invalid'}});
    expect(f.requests).toHaveLength(2);
  });
  it('keeps only the newest thirteen recent source frames', async () => {
    const rows = Array.from({length: 24}, (_, index) => {
      const date = new Date(Date.parse('2026-09-17T01:05:00Z') - index * 300_000);
      return row(date.toISOString().slice(0, 16).replace(/[-T:]/g, ''));
    });
    const f = fixture({rows}), body = await (await f.call()).json() as LiveWeatherManifest;
    expect(body.frames).toHaveLength(13); expect(body.frames.at(-1)?.source_time_kst).toBe('202609170105');
  });
});

describe('bounded read-through radar PNGs', () => {
  it('fetches only listed raw frames, validates PNG and reuses the cache', async () => {
    const f = fixture(), path = `${BASE}/radar/frames/202609170105.png`, first = await f.call(path);
    expect(first.status).toBe(200); expect(first.headers.get('Content-Type')).toBe('image/png'); expect(first.headers.get('X-Weather-Projection')).toBe(KMA_RADAR_PROJECTION.id);
    expect(Buffer.from(await first.arrayBuffer())).toEqual(VALID_PNG);
    const second = await f.call(path); expect(Buffer.from(await second.arrayBuffer())).toEqual(VALID_PNG);
    expect(f.requests).toHaveLength(3);
  });
  it('does not guess a source image URL for missing or out-of-window timestamps', async () => {
    const f = fixture();
    for (const stamp of ['202609170050', '202609160100', '202609180100']) expect((await f.call(`${BASE}/radar/frames/${stamp}.png`)).status).toBe(404);
    expect(f.requests).toHaveLength(2);
    expect((await f.call(`${BASE}/radar/frames/202609170103.png`)).status).toBe(400);
  });
  it.each([
    () => new Response('<html>upstream error</html>', {headers: {'Content-Type': 'text/html'}}),
    () => pngResponse(VALID_PNG, {status: 302, headers: {Location: 'https://evil.example/image'}}),
    () => pngResponse(VALID_PNG, {headers: {'Content-Type': 'image/png', 'Content-Length': '9999999'}}),
    () => new Response(new Uint8Array(512 * 1024 + 1), {headers: {'Content-Type': 'image/png'}}),
    () => pngResponse(png(641), {headers: {'Content-Type': 'image/png'}}),
    () => pngResponse(VALID_PNG.subarray(0, VALID_PNG.length - 3), {headers: {'Content-Type': 'image/png'}}),
    () => pngResponse(png(640, 640, pngChunk('acTL', Buffer.alloc(8))), {headers: {'Content-Type': 'image/png'}}),
  ])('rejects an invalid source image and never caches it', async image => {
    const f = fixture({image}), response = await f.call(`${BASE}/radar/frames/202609170105.png`);
    expect(response.status).toBe(502); expect(await response.json()).toMatchObject({status: 'unavailable', frames: []});
    expect([...f.responses.keys()].some(key => key.includes('/frames/'))).toBe(false);
  });
  it('checks every PNG chunk CRC and never represents source 404 as transparent rain-free weather', async () => {
    const corrupt = Buffer.from(VALID_PNG); corrupt[corrupt.length - 1] ^= 1;
    expect(() => validateRadarPng(corrupt)).toThrow(); expect(() => validateRadarPng(VALID_PNG)).not.toThrow();
    const f = fixture({image: () => new Response('missing', {status: 404})}), response = await f.call(`${BASE}/radar/frames/202609170105.png`);
    expect(response.status).toBe(404); expect(await response.json()).toMatchObject({error: {code: 'frame_missing'}, frames: []});
  });
});

describe('live source failure and cancellation', () => {
  it('cancels an in-flight source fetch when the client aborts', async () => {
    let started!: () => void; const ready = new Promise<void>(resolve => {started = resolve;});
    const fetcher: typeof fetch = (_url, init) => new Promise((_resolve, reject) => {
      init!.signal!.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), {once: true}); started();
    });
    const handler = createLiveWeatherHandler({fetcher, radarAccess: 'verified', permissionReference: PERMISSION}), controller = new AbortController();
    const pending = handler(new Request(`https://app.example${BASE}/radar`, {signal: controller.signal}));
    await ready; controller.abort(); const response = (await pending)!;
    expect(response.status).toBe(499); expect(await response.json()).toMatchObject({error: {code: 'aborted'}, frames: []});
  });
  it('bounds a hung source request and distinguishes timeout from an ordinary fetch error', async () => {
    vi.useFakeTimers();
    try {
      const fetcher: typeof fetch = (_url, init) => new Promise((_resolve, reject) => {
        init!.signal!.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')), {once: true});
      });
      const handler = createLiveWeatherHandler({fetcher, radarAccess: 'verified', permissionReference: PERMISSION, timeoutMs: 100});
      const pending = handler(new Request(`https://app.example${BASE}/radar`)); await vi.advanceTimersByTimeAsync(101);
      const response = (await pending)!; expect(response.status).toBe(504); expect(await response.json()).toMatchObject({error: {code: 'upstream_timeout'}});
    } finally {vi.useRealTimers();}
    const handler = createLiveWeatherHandler({fetcher: async () => {throw new Error('network');}, radarAccess: 'verified', permissionReference: PERMISSION});
    const response = (await handler(new Request(`https://app.example${BASE}/radar`)))!;
    expect(response.status).toBe(503); expect(await response.json()).toMatchObject({error: {code: 'upstream_unavailable'}});
  });
});

const OFFICIAL_KEY = 'fixture_service_key_1234567890';
const officialFile = (stamp = '202609170105') => `http://www.kma.go.kr/repositary/image/rdr/img/RDR_CMP_WRC_${stamp}.png`;
function officialFixture(options: {files?: unknown; code?: string; image?: () => Response; key?: string} = {}) {
  let current = INSTANT;
  const responses = new Map<string, Response>(), requests: URL[] = [];
  const cache: LiveWeatherCache = {match: async key => responses.get(key.url)?.clone(), put: async (key, value) => {responses.set(key.url, value.clone());}};
  const fetcher: typeof fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : input.toString()); requests.push(url);
    expect(init?.redirect).toBe('manual');
    if (url.hostname === 'apis.data.go.kr') return Response.json({response: {header: {resultCode: options.code ?? '00'}, body: {items: {item: [{'rdr-img-file': options.files ?? `[${officialFile('202609170100')}, ${officialFile()}]`}]}}}});
    if (url.hostname === 'www.kma.go.kr') return options.image?.() ?? pngResponse(png(635, 620), {headers: {'Content-Type': 'image/png'}});
    throw new Error('An official API fixture must not call the unverified public CGI');
  };
  const handler = createLiveWeatherHandler({fetcher, cache, now: () => current, dataGoKrServiceKey: options.key ?? OFFICIAL_KEY});
  const call = async (path = `${BASE}/radar`) => (await handler(new Request(`https://app.example${path}`)))!;
  return {call, requests, responses, advance: (milliseconds: number) => {current += milliseconds;}};
}

describe('approved RadarImgInfoService remains separate from geographic overlays', () => {
  it.each([
    `[${officialFile('202609170100')}, ${officialFile()}]`,
    JSON.stringify([officialFile('202609170100'), officialFile()]),
    [officialFile('202609170100'), officialFile()],
  ])('accepts the official list encoding and produces only panel-image descriptors', async files => {
    const f = officialFixture({files}), response = await f.call(), text = await response.text(), body = JSON.parse(text) as LiveWeatherManifest;
    expect(response.status).toBe(200); expect(body).toMatchObject({status: 'available', presentation: 'panel-image', frames: [], source: {access: 'verified', permission_reference: 'https://www.data.go.kr/data/15056924/openapi.do'}});
    expect(body.panel_frames).toHaveLength(2);
    expect(body.panel_frames?.at(-1)).toMatchObject({kind: 'radar', product: 'CMP_WRC', projection: null, map_overlay: false, image_size: [635, 620], time: '2026-09-16T16:05:00.000Z', url: `${BASE}/radar/cmp-wrc/frames/202609170105.png`});
    expect(body.panel_frames?.at(-1)?.source_url).toMatch(/^https:\/\/www\.kma\.go\.kr\/repositary\//);
    expect(isLiveWeatherFresh(body, INSTANT)).toBe(true); expect(text).not.toContain(OFFICIAL_KEY);
    expect(f.requests).toHaveLength(1); expect(f.requests[0].pathname).toBe('/1360000/RadarImgInfoService/getCmpImg');
    expect(f.requests[0].searchParams.get('ServiceKey')).toBe(OFFICIAL_KEY); expect(f.requests[0].searchParams.get('data')).toBe('CMP_WRC');
    expect([...f.responses.keys()].join(' ')).not.toContain(OFFICIAL_KEY);
  });
  it('validates the 635×620 official PNG without interpreting its legend/map as rain pixels', async () => {
    const f = officialFixture(), path = `${BASE}/radar/cmp-wrc/frames/202609170105.png`, response = await f.call(path);
    expect(response.status).toBe(200); expect(response.headers.get('X-Weather-Representation')).toBe('panel-image');
    expect(response.headers.get('X-Weather-Map-Overlay')).toBe('false'); expect(response.headers.get('X-Weather-Projection')).toBeNull();
    expect(Buffer.from(await response.arrayBuffer())).toEqual(png(635, 620)); await f.call(path); expect(f.requests).toHaveLength(2);
    expect(f.requests[1].protocol).toBe('https:'); expect(f.requests[1].search).toBe('');
  });
  it('does not authorize the unrelated SFC-HSR public CGI just because a service key exists', async () => {
    const f = officialFixture(), response = await f.call(`${BASE}/radar/frames/202609170105.png`);
    expect(response.status).toBe(503); expect(await response.json()).toMatchObject({error: {code: 'source_not_verified'}, frames: []});
    expect(f.requests).toHaveLength(0);
  });
  it('marks a delayed official observation stale and removes both display paths', async () => {
    const f = officialFixture({files: [officialFile('202609170040')]}), response = await f.call();
    expect(response.status).toBe(503); expect(await response.json()).toMatchObject({status: 'stale', presentation: 'none', frames: [], panel_frames: [], error: {code: 'stale'}});
  });
  it.each(['http://evil.example/repositary/image/rdr/img/RDR_CMP_WRC_202609170105.png', `${officialFile()}?ServiceKey=secret`, officialFile().replace('CMP_WRC', 'CMP_OTHER'), 'http://user:secret@www.kma.go.kr/repositary/image/rdr/img/RDR_CMP_WRC_202609170105.png'])('rejects untrusted official result URLs %s', async file => {
    const f = officialFixture({files: [file]}), response = await f.call(), body = await response.text();
    expect(response.status).toBe(502); expect(body).not.toContain(file); expect(f.requests).toHaveLength(1);
  });
  it.each([['30', 'authentication_failed'], ['22', 'upstream_quota'], ['03', 'frame_missing'], ['99', 'upstream_invalid']])('separates official API result code %s from an empty observation', async (code, error) => {
    const f = officialFixture({code}), response = await f.call(), text = await response.text();
    expect(JSON.parse(text)).toMatchObject({status: 'unavailable', frames: [], panel_frames: [], error: {code: error}});
    expect(text).not.toContain(OFFICIAL_KEY); expect(f.responses.size).toBe(0);
  });
  it('rejects a geographic-product image returned for the panel product', async () => {
    const f = officialFixture({image: () => pngResponse(VALID_PNG, {headers: {'Content-Type': 'image/png'}})}), response = await f.call(`${BASE}/radar/cmp-wrc/frames/202609170105.png`);
    expect(response.status).toBe(502); expect(await response.json()).toMatchObject({error: {code: 'upstream_invalid'}, panel_frames: []});
    expect([...f.responses.keys()].some(key => key.includes('/frames/'))).toBe(false);
  });
  it('never turns absent server credentials into a request containing an empty key', async () => {
    const f = fixture({access: 'unverified'}), response = await f.call(`${BASE}/radar/cmp-wrc/frames/202609170105.png`);
    expect(response.status).toBe(503); expect(await response.json()).toMatchObject({error: {code: 'authentication_required'}}); expect(f.fetcher).not.toHaveBeenCalled();
  });
});
