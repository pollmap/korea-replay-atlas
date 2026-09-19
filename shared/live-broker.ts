import type {LiveTransitSnapshot} from './live-transit';

/** Private Service binding protocol. These paths are never public map routes. */
export const LIVE_BROKER_OBJECT_NAME='transit-production';
export const LIVE_BROKER_PATH='/v1/snapshot';
export const LIVE_BROKER_STATUS_PATH='/v1/status';
/** Entire UTF-8 JSON response envelope, including snapshot metadata. */
export const LIVE_BROKER_MAX_SNAPSHOT_BYTES=512*1024;
export const LIVE_BROKER_MAX_REQUEST_BYTES=2048;
export const LIVE_BROKER_REQUEST_BODY_TIMEOUT_MS=2000;
export const LIVE_BROKER_SOURCE_TIMEOUT_MS=8000;
export const LIVE_BROKER_MAX_SOURCE_TIMEOUT_MS=15_000;
/** Binding callers apply this deadline through receipt of the whole response body. */
export const LIVE_BROKER_RESPONSE_TIMEOUT_MS=20_000;
export const LIVE_BROKER_TTL_SECONDS={bus:90,subway:120} as const;

export type BrokerTarget={kind:'bus';city_code:string;route_id:string}|{kind:'subway';subway_id:string;name:string};
export interface BrokerRequest {protocol:1;target:BrokerTarget;}
export interface BrokerQuota {
  daily_limit:number;local_budget:number;budget_limit:number;
  guard:'durable-object';global_enforced:true;scope:'broker-mediated-requests';window:'rolling-24h-conservative';
}
export type BrokerSnapshot=Omit<LiveTransitSnapshot,'quota'>&{quota:BrokerQuota};
export type BrokerErrorCode='invalid_request'|'not_configured'|'busy'|'quota_exceeded'|'broker_unavailable'|'upstream_auth'|'upstream_http'|'upstream_timeout'|'upstream_invalid'|'aborted';
export interface BrokerFailureResult {
  protocol:1;ok:false;httpStatus:number;error:{code:BrokerErrorCode;retry_after_seconds:number};
  /** Present only after this broker validates the snapshot target and its policy. */
  quota?:BrokerQuota;
}
export type BrokerResult={protocol:1;ok:true;httpStatus:number;snapshot:BrokerSnapshot}|BrokerFailureResult;
export interface BrokerSqlDiagnostics {
  scope:'activation';source:'sql-cursor';
  /** A random identifier only for comparing counters from the same activation. */
  activation_id:string;
  /** Native cursor totals; null when any SQL cursor cannot be reliably measured. */
  sql_rows_read:number|null;sql_rows_written:number|null;
  includes_initialization:true;includes_status_reads:true;
}
export interface BrokerStoredStatus {
  /** Persisted reservations in the same conservative window used to grant HTTP calls. */
  reserved:{bus:number;subway:number};
  /** Slots still held by this activation or by an unexpired prior activation lease. */
  active_leases:number;
  /** Cumulative SQL API counters, not durable/account-wide billing totals. */
  diagnostics?:BrokerSqlDiagnostics;
}
export interface BrokerStatus extends BrokerStoredStatus {
  protocol:1;ok:true;httpStatus:200;
  /** Presence and format only; this endpoint never verifies a credential upstream. */
  configured:{bus:boolean;subway:boolean};
  guard:'durable-object';global_enforced:true;scope:'broker-mediated-requests';window:'rolling-24h-conservative';
  ttl_seconds:typeof LIVE_BROKER_TTL_SECONDS;
  daily_limits:{bus:number;subway:number};
  budget_limits:{bus:number;subway:number};
}
export type BrokerStatusResult=BrokerStatus|BrokerFailureResult;
