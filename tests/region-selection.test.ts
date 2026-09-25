import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {
  administrativeDongBoundary, clearRegionBoundaryCache, decodeRegionRing, regionAdministrativeDongs,
  REGION_SELECTION_SOURCE, regionBoundaryCacheSnapshot, selectedRegionBoundary, selectedSgisBoundary,
} from '../src/region-selection';
import regions from '../src/data/region-selection.json';
import dongs from '../src/data/region-selection-dongs.json';

const date = '2025-06-30';
function source(name: string): Uint8Array {
  const folder = name.startsWith('boundary-') ? 'region-selection-areas' : 'region-selection-dongs';
  return readFileSync(new URL(`../src/data/${folder}/${name}`, import.meta.url));
}
const realFetch = vi.fn(async (input: string | URL | Request) => {
  const match = String(input).match(/(boundary-\d+|dongs-\d+)\.json/);
  if (!match) throw new Error('Unexpected boundary URL');
  return new Response(new Uint8Array(source(`${match[1]}.json`)));
});
beforeEach(() => {clearRegionBoundaryCache(); realFetch.mockClear(); vi.stubGlobal('fetch', realFetch);});
afterEach(() => vi.unstubAllGlobals());

describe('selected administrative area geometry', () => {
  it('highlights actual Seodaemun geometry using the complete name without a legal-code crosswalk', async () => {
    const result = (await selectedRegionBoundary('서울특별시 서대문구', date))!;
    expect(result.id).toBe('sgis:20250630:sigungu:11130');
    expect(result.geometry.type).toBe('MultiPolygon');
    expect(result.properties.code_namespace).toBe('SGIS administrative');
    expect(result.geometry.coordinates.flat(2).every(([lon, lat]) => lon > 126.8 && lon < 127 && lat > 37.5 && lat < 37.7)).toBe(true);
    expect(await selectedRegionBoundary('서대문구', date)).toBeNull();
    expect(await selectedRegionBoundary('서울특별시 영종구', date)).toBeNull();
    expect(realFetch).toHaveBeenCalledTimes(1);
  });
  it('refuses stale dates, partial names and invented boundaries before downloading', async () => {
    expect(await selectedRegionBoundary('서울특별시 송파구', '2026-01-01')).toBeNull();
    expect(await selectedRegionBoundary('송파구', date)).toBeNull();
    expect(await selectedRegionBoundary('인천광역시 영종구', date)).toBeNull();
    expect(await selectedSgisBoundary('11710', undefined)).toBeNull();
    expect((await regionAdministrativeDongs('서울특별시 서대문구', undefined)).features).toHaveLength(0);
    expect(realFetch).not.toHaveBeenCalled();
  });
  it('keeps all audited identities, original islands and valid closed rings in version-pinned chunks', () => {
    expect(regions.featureCount).toBe(269); expect(dongs.featureCount).toBe(3559);
    expect(REGION_SELECTION_SOURCE.maximumDisplayErrorMetres).toBeLessThanOrEqual(20.02);
    for (const [index, prefix] of [[regions, 'boundary'], [dongs, 'dongs']] as const) {
      let count = 0;
      for (const [code, bytes, sha] of index.chunks as [string, number, string][]) {
        const body = source(`${prefix}-${code}.json`);
        expect(body.byteLength).toBe(bytes);
        expect(createHash('sha256').update(body).digest('hex')).toBe(sha);
        const rows = JSON.parse(new TextDecoder().decode(body)) as [string, string, number, string[][]][];
        count += rows.length;
        for (const row of rows) {
          expect(row[0].startsWith(code)).toBe(true);
          for (const polygon of row[3]) for (const ring of polygon) {
            const coordinates = decodeRegionRing(ring, row[2]);
            expect(coordinates[0]).toEqual(coordinates.at(-1));
            expect(coordinates.every(([lon, lat]) => Number.isFinite(lon) && Number.isFinite(lat) && lon > 124 && lon < 132 && lat > 32 && lat < 40)).toBe(true);
          }
        }
      }
      expect(count).toBe(index.featureCount);
    }
  },30000);
  it('preserves offshore islands, including Dokdo, within the Ulleung display boundary', async () => {
    const result = (await selectedRegionBoundary('경상북도 울릉군', date))!;
    expect(result.geometry.coordinates.length).toBeGreaterThan(2);
    expect(result.geometry.coordinates.flat(2).some(([lon]) => lon > 131.8)).toBe(true);
  });
  it('returns administrative dongs for a selected district and distinguishes them from legal dongs', async () => {
    const features = (await regionAdministrativeDongs('서울특별시 서대문구', date)).features;
    expect(features.length).toBe(14);
    expect(features.some(feature => feature.properties.name === '충현동')).toBe(true);
    for (const feature of features) {
      expect(feature.properties.boundary_kind).toBe('administrative-dong');
      expect(feature.properties.admin_code.startsWith('11130')).toBe(true);
      expect(await administrativeDongBoundary(feature.properties.admin_code, date)).toEqual(feature);
      expect(await selectedSgisBoundary(feature.properties.admin_code, date)).toEqual(feature);
    }
    expect(realFetch).toHaveBeenCalledTimes(1);
    expect((await regionAdministrativeDongs('서울특별시', date)).features).toHaveLength(0);
    expect((await regionAdministrativeDongs('세종특별자치시', date)).features.length).toBeGreaterThan(20);
  });
  it('rejects corrupt network payloads and permits a clean retry', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('wrong')));
    await expect(selectedRegionBoundary('서울특별시 서대문구', date)).rejects.toThrow('size');
    expect(regionBoundaryCacheSnapshot().bytes).toBe(0);
    const bytes = source('boundary-11130.json');
    bytes[0] = 32;
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Uint8Array(bytes))));
    await expect(selectedRegionBoundary('서울특별시 서대문구', date)).rejects.toThrow('SHA');
    vi.stubGlobal('fetch', realFetch);
    expect(await selectedRegionBoundary('서울특별시 서대문구', date)).not.toBeNull();
  });
  it('bounds retained bytes during repeated regional changes and clears the cache', async () => {
    for (const [code] of regions.regions) {
      await selectedSgisBoundary(code, date);
      expect(regionBoundaryCacheSnapshot().bytes).toBeLessThanOrEqual(4 * 1024 * 1024);
    }
    clearRegionBoundaryCache(); expect(regionBoundaryCacheSnapshot().bytes).toBe(0);
  });
  it('does not fetch a selection that has already been cancelled', async () => {
    const controller = new AbortController(); controller.abort();
    await expect(selectedRegionBoundary('서울특별시 서대문구', date, controller.signal)).rejects.toMatchObject({name: 'AbortError'});
    expect(realFetch).not.toHaveBeenCalled();
  });
  it('rejects broken coordinate streams instead of displaying malformed boundaries', () => {
    expect(() => decodeRegionRing('_', 7)).toThrow();
    expect(() => decodeRegionRing('!', 7)).toThrow();
    expect(() => decodeRegionRing('??', 7)).toThrow();
  });
});
