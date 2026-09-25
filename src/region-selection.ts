import type {Feature, FeatureCollection, MultiPolygon} from 'geojson';
import data from './data/region-selection.json';
import dongData from './data/region-selection-dongs.json';

type CompactRegion = [code: string, name: string, precision: number, polygons: string[][]];
type ChunkRef = [code: string, bytes: number, sha256: string];
export interface RegionBoundaryProperties {
  name: string;
  admin_code: string;
  source_record_id: string;
  code_namespace: 'SGIS administrative';
  boundary_reference_date: string;
  boundary_kind: 'sido' | 'sigungu' | 'administrative-dong';
  maximum_display_error_metres: number;
}
export type RegionBoundaryFeature = Feature<MultiPolygon, RegionBoundaryProperties>;
const normalize = (name: string) => name.normalize('NFC').trim().replace(/\s+/g, ' ');
const byName = new Map((data.regions as [string, string][]).map(row => [normalize(row[1]), row[0]]));
const areaRefs = new Map((data.chunks as ChunkRef[]).map(ref => [ref[0], ref]));
const dongRefs = new Map((dongData.chunks as ChunkRef[]).map(ref => [ref[0], ref]));
// URL imports keep source coordinates out of the initial JavaScript bundle.
const areaUrls = import.meta.glob<string>('./data/region-selection-areas/*.json', {query: '?url&no-inline', import: 'default', eager: true});
const dongUrls = import.meta.glob<string>('./data/region-selection-dongs/*.json', {query: '?url&no-inline', import: 'default', eager: true});
const cache = new Map<string, ArrayBuffer>();
const CACHE_LIMIT = 4 * 1024 * 1024;
let cachedBytes = 0;

export const REGION_SELECTION_SOURCE = {
  referenceDate: data.referenceDate,
  namespace: data.namespace,
  sourceUrl: data.sourceUrl,
  archiveSha256: data.archiveSha256,
  maximumDisplayErrorMetres: data.maximumDisplayErrorMetres,
  purpose: 'selected-area-display-only',
} as const;

/** Closed longitude/latitude polyline; arithmetic avoids signed 32-bit overflow. */
export function decodeRegionRing(encoded: string, precision: number): number[][] {
  if (!Number.isInteger(precision) || precision < 0 || precision > 9) throw new Error('Invalid coordinate precision');
  const result: number[][] = [];
  const point = [0, 0];
  let axis = 0, number = 0, multiplier = 1;
  for (const char of encoded) {
    const value = char.charCodeAt(0) - 63;
    if (value < 0 || value > 63 || multiplier > 2 ** 50) throw new Error('Invalid region coordinate');
    number += (value % 32) * multiplier;
    if (value >= 32) { multiplier *= 32; continue; }
    point[axis] += number % 2 ? -(number + 1) / 2 : number / 2;
    if (axis === 1) result.push([point[0] / 10 ** precision, point[1] / 10 ** precision]);
    axis = 1 - axis; number = 0; multiplier = 1;
  }
  if (axis || multiplier !== 1 || result.length < 4 || result[0][0] !== result.at(-1)![0] || result[0][1] !== result.at(-1)![1]) {
    throw new Error('Incomplete or unclosed region ring');
  }
  return result;
}

function feature(row: CompactRegion): RegionBoundaryFeature {
  const [code, name, precision, polygons] = row;
  const level = code.length === 2 ? 'sido' : code.length === 5 ? 'sigungu' : 'eupmyeondong';
  return {type: 'Feature', id: `sgis:20250630:${level}:${code}`, properties: {
    name, admin_code: code, source_record_id: `sgis:20250630:${level}:${code}`,
    code_namespace: 'SGIS administrative', boundary_reference_date: data.referenceDate,
    boundary_kind: level === 'eupmyeondong' ? 'administrative-dong' : level,
    maximum_display_error_metres: level === 'eupmyeondong' ? dongData.maximumDisplayErrorMetres : data.maximumDisplayErrorMetres,
  }, geometry: {type: 'MultiPolygon', coordinates: polygons.map(polygon => polygon.map(ring => decodeRegionRing(ring, precision)))}};
}

async function load(code: string, dongs: boolean, signal?: AbortSignal): Promise<CompactRegion[]> {
  signal?.throwIfAborted();
  const ref = (dongs ? dongRefs : areaRefs).get(code);
  if (!ref) return [];
  const path = dongs ? `./data/region-selection-dongs/dongs-${code}.json` : `./data/region-selection-areas/boundary-${code}.json`;
  const url = (dongs ? dongUrls : areaUrls)[path];
  if (!url || ref[1] > 1024 * 1024 || ref[1] <= 0) throw new Error('Invalid administrative boundary reference');
  let body = cache.get(ref[2]);
  if (body) { cache.delete(ref[2]); cache.set(ref[2], body); }
  else {
    const controller = new AbortController();
    const cancel = () => controller.abort(signal?.reason);
    signal?.addEventListener('abort', cancel, {once: true});
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(url, {signal: controller.signal, credentials: 'omit', redirect: 'error'});
      if (response.status !== 200) { await response.body?.cancel(); throw new Error(`Administrative boundary HTTP ${response.status}`); }
      const reader = response.body?.getReader();
      if (!reader) throw new Error('Empty administrative boundary response');
      const chunks: Uint8Array[] = [];
      let bytes = 0;
      try {
        while (true) {
          const next = await reader.read(); if (next.done) break;
          bytes += next.value.byteLength;
          if (bytes > ref[1]) throw new Error('Administrative boundary exceeds declared size');
          chunks.push(next.value);
        }
      } catch (error) { await reader.cancel().catch(() => undefined); throw error; }
      finally { reader.releaseLock(); }
      if (bytes !== ref[1]) throw new Error('Administrative boundary size differs');
      const result = new Uint8Array(bytes);
      let at = 0; for (const chunk of chunks) { result.set(chunk, at); at += chunk.byteLength; }
      const sha = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', result.buffer)), value => value.toString(16).padStart(2, '0')).join('');
      if (sha !== ref[2]) throw new Error('Administrative boundary SHA differs');
      controller.signal.throwIfAborted();
      body = result.buffer;
      while (cachedBytes + body.byteLength > CACHE_LIMIT && cache.size) {
        const [key, old] = cache.entries().next().value!; cache.delete(key); cachedBytes -= old.byteLength;
      }
      const previous = cache.get(ref[2]);
      if (previous) cachedBytes -= previous.byteLength;
      cache.set(ref[2], body); cachedBytes += body.byteLength;
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
  }
  signal?.throwIfAborted();
  return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(body)) as CompactRegion[];
}

/** Exact full-name display lookup. This is deliberately not a legal-code crosswalk. */
export async function selectedRegionBoundary(name: string, referenceDate: string | undefined, signal?: AbortSignal): Promise<RegionBoundaryFeature | null> {
  if (referenceDate !== data.referenceDate) return null;
  const code = byName.get(normalize(name));
  if (!code) return null;
  const row = (await load(code, false, signal)).find(row => row[0] === code);
  return row ? feature(row) : null;
}

/** Administrative dongs in one selected district; never substitutes legal-dong polygons. */
export async function regionAdministrativeDongs(name: string, referenceDate: string | undefined, signal?: AbortSignal): Promise<FeatureCollection<MultiPolygon, RegionBoundaryProperties>> {
  const empty: FeatureCollection<MultiPolygon, RegionBoundaryProperties> = {type: 'FeatureCollection', features: []};
  if (referenceDate !== data.referenceDate || referenceDate !== dongData.referenceDate) return empty;
  const code = byName.get(normalize(name));
  // Sejong has no borough selection in the property UI; SGIS has one 29010 parent.
  const district = code === '29' ? '29010' : code;
  if (!district || district.length !== 5) return empty;
  return {type: 'FeatureCollection', features: (await load(district, true, signal)).map(feature)};
}

/** Only pass SGIS codes read from a same-release administrative map feature. */
export async function selectedSgisBoundary(code: string, referenceDate: string | undefined, signal?: AbortSignal): Promise<RegionBoundaryFeature | null> {
  if (referenceDate !== data.referenceDate) return null;
  const dongs = code.length === 8;
  const row = (await load(dongs ? code.slice(0, 5) : code, dongs, signal)).find(row => row[0] === code);
  return row ? feature(row) : null;
}

export async function administrativeDongBoundary(code: string, referenceDate: string | undefined, signal?: AbortSignal): Promise<RegionBoundaryFeature | null> {
  return code.length === 8 ? selectedSgisBoundary(code, referenceDate, signal) : null;
}

export function regionBoundaryCacheSnapshot() { return {bytes: cachedBytes, chunks: cache.size, limitBytes: CACHE_LIMIT}; }
export function clearRegionBoundaryCache() { cache.clear(); cachedBytes = 0; }
