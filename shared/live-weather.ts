/** Transient observations. These are never substituted for a pinned replay release. */
export const LIVE_WEATHER_API_PREFIX = '/api/v1/live/weather';
export const LIVE_WEATHER_REFRESH_SECONDS = 60;
export const LIVE_RADAR_MAX_AGE_SECONDS = 20 * 60;
export const LIVE_RADAR_LOOKBACK_SECONDS = 2 * 60 * 60;

export type LiveWeatherKind = 'radar' | 'satellite';
export type LiveWeatherStatus = 'available' | 'stale' | 'unavailable';

export interface LiveWeatherProjection {
  id: 'kma-radar-lcc-640-v1';
  crs: string;
  /** Source image bounds in projected metres: west, south, east, north. */
  extent: readonly [number, number, number, number];
  width: 640;
  height: 640;
  origin: 'top-left';
  metadata_url: string;
}

export const KMA_RADAR_PROJECTION: Readonly<LiveWeatherProjection> = Object.freeze({
  id: 'kma-radar-lcc-640-v1',
  crs: '+proj=lcc +lat_1=30 +lat_2=60 +lat_0=0 +lon_0=126 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs',
  extent: Object.freeze([-440000, 3797382.7212162036, 584000, 4821382.721216239] as const),
  width: 640,
  height: 640,
  origin: 'top-left',
  metadata_url: 'https://www.weather.go.kr/w/resources/js/kmap.bb.js?ver=202607091110',
});

export interface RawLiveFrame {
  kind: 'radar';
  product: 'SFC-HSR';
  /** Actual source observation timestamp, UTC. Not the fetch timestamp. */
  time: string;
  source_time_kst: string;
  /** Same-origin proxy path. Original LCC image; reproject before map display. */
  url: string;
  source_url: string;
  projection: LiveWeatherProjection;
}

/** Official full-frame presentation with map/legend, not a georeferenced overlay. */
interface PanelFrameBase {
  representation: 'panel-image';
  map_overlay: false;
  projection: null;
  time: string;
  source_time_kst: string;
  url: string;
  source_url: string;
}

export interface RadarPanelLiveFrame extends PanelFrameBase {
  kind: 'radar';
  product: 'CMP_WRC';
  image_size: readonly [635, 620];
}

export interface SatellitePanelLiveFrame extends PanelFrameBase {
  kind: 'satellite';
  product: 'GK2A_IR105_KO';
  /** The filename uses UTC, unlike the radar filename. */
  source_time_utc: string;
  /** The API list does not declare dimensions; the PNG route validates bounded IHDR dimensions. */
  image_size: null;
}

export type PanelLiveFrame = RadarPanelLiveFrame | SatellitePanelLiveFrame;

export type LiveWeatherErrorCode =
  | 'source_not_verified' | 'projection_unverified' | 'projection_changed'
  | 'upstream_http' | 'upstream_timeout' | 'upstream_invalid' | 'upstream_unavailable'
  | 'authentication_required' | 'authentication_failed' | 'upstream_quota'
  | 'frame_missing' | 'stale' | 'invalid_request' | 'aborted';

export interface LiveWeatherError {
  code: LiveWeatherErrorCode;
  message: string;
  retryable: boolean;
}

export interface LiveWeatherManifest {
  schema_version: 1;
  mode: 'live';
  kind: LiveWeatherKind;
  status: LiveWeatherStatus;
  /** Last successful upstream list check. Null means no successful source check. */
  checked_at: string | null;
  /** Timestamp of this response. Cache hits must preserve checked_at. */
  served_at: string;
  latest_observed_at: string | null;
  max_age_seconds: number;
  refresh_after_seconds: number;
  /** Empty if stale, unavailable, or deployment rights/projection are unverified. */
  frames: RawLiveFrame[];
  /** Distinct from map frames: never send these to the map reprojection Worker. */
  panel_frames?: PanelLiveFrame[];
  presentation?: 'map-overlay' | 'panel-image' | 'none';
  source: {
    name: string;
    page_url: string;
    copyright_url: string;
    access: 'verified' | 'unverified';
    /** Specific permission/source-policy evidence supplied by the deployment. */
    permission_reference: string | null;
  };
  interpretation: {
    representation: 'official-color';
    numeric_inversion: false;
    background_meaning: 'no-echo-or-missing-unknown' | 'source-rendering-not-classified';
  };
  error?: LiveWeatherError;
}

/** A client clock can expire a previously fresh response without fetching again. */
export function isLiveWeatherFresh(manifest: LiveWeatherManifest, now = Date.now()): boolean {
  if (manifest.status !== 'available' || !manifest.latest_observed_at || !manifest.checked_at || !Number.isFinite(now)
    || !Number.isFinite(manifest.max_age_seconds) || manifest.max_age_seconds <= 0 || manifest.max_age_seconds > LIVE_RADAR_MAX_AGE_SECONDS
    || (manifest.frames.length === 0 && !manifest.panel_frames?.length)) return false;
  const observed = Date.parse(manifest.latest_observed_at), checked = Date.parse(manifest.checked_at);
  return Number.isFinite(observed) && Number.isFinite(checked)
    && observed <= now && checked <= now + 30_000
    && now - observed <= manifest.max_age_seconds * 1000;
}
