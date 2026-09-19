"""Bounded, restartable official TAGO route catalog; never polls vehicle positions.

python -m pipeline.live_transit_catalog --checkpoint .local/live-transit/20260917
Only the approved route/city list operations are requested. Keys, authenticated
URLs and upstream exception strings are excluded from files and console output.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1
PAGE_SIZE = 1000
MAX_PAGES = 10
MAX_BODY = 2 * 1024 * 1024
SERVICES = {
    "routes": "https://apis.data.go.kr/1613000/BusRouteInfoInqireService/",
    "locations": "https://apis.data.go.kr/1613000/BusLcInfoInqireService/",
}
SOURCE = "https://www.data.go.kr/data/15098529/openapi.do"
LOCATION_SOURCE = "https://www.data.go.kr/data/15098533/openapi.do"


class CatalogFailure(Exception):
    def __init__(self, code: str, *, retryable: bool = False, stop: bool = False):
        super().__init__(code)
        self.code, self.retryable, self.stop = code, retryable, stop


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_new(path: Path, data: bytes) -> None:
    """Create a derived immutable file, checking rather than replacing an original."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise CatalogFailure("immutable_file_conflict")
        return
    temporary = path.with_name(path.name + ".pending-" + uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    # Windows rename cannot replace an existing destination; a concurrent writer
    # therefore fails closed, preserving both original and temporary evidence.
    temporary.rename(path)


def clean_text(value: Any, maximum: int = 120, *, optional: bool = False) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if optional and (value is None or value == ""):
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CatalogFailure("invalid_text")
    return value.strip()


def integer(value: Any) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise CatalogFailure("invalid_integer")
    return int(value)


def body_items(root: Any) -> tuple[dict, list[dict]]:
    if not isinstance(root, dict):
        raise CatalogFailure("invalid_json_contract")
    response = root.get("response")
    if not isinstance(response, dict):
        raise CatalogFailure("invalid_json_contract")
    header = response.get("header")
    if not isinstance(header, dict):
        raise CatalogFailure("invalid_json_contract")
    check_code(header.get("resultCode"))
    body = response.get("body")
    if not isinstance(body, dict):
        raise CatalogFailure("invalid_json_contract")
    container = body.get("items")
    if container in (None, ""):
        rows = []
    elif isinstance(container, dict):
        value = container.get("item")
        rows = [] if value is None else value if isinstance(value, list) else [value]
    else:
        raise CatalogFailure("invalid_items")
    if not all(isinstance(row, dict) for row in rows):
        raise CatalogFailure("invalid_items")
    return body, rows


def check_code(value: Any) -> None:
    code = str(value)
    if code in ("0", "00"):
        return
    if code in ("20", "29", "30", "31", "401", "403"):
        raise CatalogFailure("upstream_auth", stop=True)
    if code in ("22", "23", "429"):
        raise CatalogFailure("upstream_quota", stop=True)
    raise CatalogFailure("upstream_code", retryable=code in ("01", "04", "05"))


def normalize_cities(root: Any) -> list[dict]:
    body, rows = body_items(root)
    if not rows or len(rows) > 300:
        raise CatalogFailure("city_count_limit")
    if body.get("totalCount") is not None and integer(body["totalCount"]) != len(rows):
        raise CatalogFailure("city_pagination_incomplete")
    result, seen = [], set()
    for row in rows:
        code, name = clean_text(row.get("citycode"), 8), clean_text(row.get("cityname"), 80)
        if not re.fullmatch(r"\d{1,8}", code) or code in seen:
            raise CatalogFailure("city_identity_conflict")
        seen.add(code)
        result.append({"city_code": code, "city_name": name})
    return sorted(result, key=lambda item: int(item["city_code"]))


def normalize_route(row: dict, city: dict) -> dict:
    route_id = clean_text(row.get("routeid"), 60)
    if not re.fullmatch(r"[A-Za-z0-9가-힣_-]{1,60}", route_id):
        raise CatalogFailure("invalid_route_id")
    number = clean_text(row.get("routeno"), 60)
    # Route numbers alone are not unique: retain provider ID for each direction.
    return {"id": f"tago-{city['city_code']}-{route_id.lower()}", "city_code": city["city_code"],
            "route_id": route_id, "route_no": number, "label": f"{city['city_name']} {number}",
            "route_type": clean_text(row.get("routetp"), 60, optional=True),
            "start_station_name": clean_text(row.get("startnodenm"), 120, optional=True),
            "end_station_name": clean_text(row.get("endnodenm"), 120, optional=True)}


class OfficialClient:
    def __init__(self, key: str, checkpoint: Path, *, max_requests: int = 500, retries: int = 1,
                 min_interval: float = .5, session_factory=requests.Session):
        if not key or len(key) < 8 or any(c.isspace() for c in key):
            raise CatalogFailure("credential_missing")
        if max_requests < 1 or max_requests > 500 or retries not in (0, 1) or min_interval < .5:
            raise CatalogFailure("invalid_request_budget")
        self.key, self.checkpoint = key, checkpoint
        self.max_requests, self.retries, self.min_interval = max_requests, retries, min_interval
        self.session_factory, self.local = session_factory, threading.local()
        self.lock, self.stopped = threading.Lock(), threading.Event()
        self.last_started, self.requests_started, self.cache_hits = 0.0, 0, 0
        self.checkpoint.mkdir(parents=True, exist_ok=True)
        self.ledger = checkpoint / "requests.jsonl"
        if self.ledger.exists():
            for line in self.ledger.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    if row.get("schema_version") != SCHEMA or row.get("ordinal") != self.requests_started + 1:
                        raise ValueError
                    self.requests_started += 1
                except (ValueError, AttributeError):
                    raise CatalogFailure("request_ledger_invalid") from None

    def reserve(self, service: str, operation: str, params: dict) -> None:
        with self.lock:
            if self.stopped.is_set():
                raise CatalogFailure("collection_stopped")
            if self.requests_started >= self.max_requests:
                self.stopped.set()
                raise CatalogFailure("request_budget_exhausted", stop=True)
            delay = self.min_interval - (time.monotonic() - self.last_started)
            if delay > 0:
                time.sleep(delay)
            self.last_started = time.monotonic()
            self.requests_started += 1
            event = {"schema_version": SCHEMA, "ordinal": self.requests_started, "started_at": utc_now(),
                     "service": service, "operation": operation, "conditions": params}
            with self.ledger.open("ab") as stream:
                stream.write(canonical(event) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())

    def request(self, service: str, operation: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        if service not in SERVICES or operation not in ("getCtyCodeList", "getRouteNoList") or (service == "locations" and operation != "getCtyCodeList"):
            raise CatalogFailure("unapproved_operation")
        if operation == "getCtyCodeList" and params or operation == "getRouteNoList" and (set(params) != {"cityCode", "pageNo", "numOfRows"} or not re.fullmatch(r"\d{1,8}", str(params["cityCode"])) or integer(params["pageNo"]) not in range(1, MAX_PAGES + 1) or integer(params["numOfRows"]) != PAGE_SIZE):
            raise CatalogFailure("invalid_request_conditions")
        identity = {"service": service, "operation": operation, "conditions": params}
        stamp = sha256(canonical(identity)).hexdigest()[:24]
        path = self.checkpoint / "pages" / f"{service}-{operation}-{stamp}.json"
        if path.exists():
            try:
                value = json.loads(path.read_bytes())
                if value["identity"] != identity or value["schema_version"] != SCHEMA or sha256(canonical(value["payload"])).hexdigest() != value["payload_sha256"]:
                    raise ValueError
                body_items(value["payload"])
            except (KeyError, ValueError, TypeError):
                raise CatalogFailure("checkpoint_invalid") from None
            with self.lock:
                self.cache_hits += 1
            return value
        for attempt in range(self.retries + 1):
            self.reserve(service, operation, params)
            try:
                if not hasattr(self.local, "session"):
                    self.local.session = self.session_factory()
                started = time.monotonic()
                with self.local.session.get(SERVICES[service] + operation,
                        params={**params, "_type": "json", "serviceKey": self.key},
                        headers={"Accept": "application/json", "User-Agent": "KoreaReplay/1 official-route-catalog"},
                        timeout=(5, 15), allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        if response.status_code in (401, 403, 429):
                            check_code(response.status_code)
                        raise CatalogFailure("upstream_http", retryable=response.status_code >= 500)
                    if integer(response.headers.get("Content-Length", "0")) > MAX_BODY:
                        raise CatalogFailure("response_limit")
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        if time.monotonic() - started > 30:
                            raise CatalogFailure("upstream_timeout", retryable=True)
                        data.extend(chunk)
                        if len(data) > MAX_BODY:
                            raise CatalogFailure("response_limit")
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        raise CatalogFailure("invalid_encoding") from None
                    if self.key in text or quote(self.key, safe="") in text:
                        raise CatalogFailure("reflected_credential", stop=True)
                    if text.lstrip().startswith("<"):
                        code = re.search(r"<returnReasonCode>\s*(\d{1,3})\s*</returnReasonCode>", text)
                        if code:
                            check_code(code[1])
                        raise CatalogFailure("invalid_json")
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        raise CatalogFailure("invalid_json") from None
                    gateway = payload.get("OpenAPI_ServiceResponse", {}).get("cmmMsgHeader") if isinstance(payload, dict) else None
                    if isinstance(gateway, dict):
                        check_code(gateway.get("returnReasonCode"))
                    body_items(payload)
                    value = {"schema_version": SCHEMA, "identity": identity, "retrieved_at": utc_now(),
                             "raw_sha256": sha256(data).hexdigest(), "raw_bytes": len(data),
                             "payload_sha256": sha256(canonical(payload)).hexdigest(), "payload": payload}
                    atomic_new(path, canonical(value))
                    return value
            except requests.RequestException:
                failure = CatalogFailure("upstream_network", retryable=True)
            except CatalogFailure as error:
                failure = error
            if failure.stop:
                self.stopped.set()
            if not failure.retryable or attempt >= self.retries or self.stopped.is_set():
                raise failure from None
            time.sleep(1)
        raise CatalogFailure("upstream_failed")


def collect_city(client: OfficialClient, city: dict, location_codes: set[str]) -> dict:
    routes, ids, expected, retrieved, proofs = [], set(), None, None, []
    for page in range(1, MAX_PAGES + 1):
        response = client.request("routes", "getRouteNoList", {"cityCode": city["city_code"], "pageNo": page, "numOfRows": PAGE_SIZE})
        body, rows = body_items(response["payload"])
        total = integer(body.get("totalCount"))
        if total > PAGE_SIZE * MAX_PAGES:
            raise CatalogFailure("city_route_count_limit")
        if integer(body.get("pageNo")) != page or integer(body.get("numOfRows")) != PAGE_SIZE or expected is not None and total != expected:
            raise CatalogFailure("page_metadata_inconsistent")
        expected = total
        if len(rows) != min(PAGE_SIZE, max(0, total - len(routes))):
            raise CatalogFailure("page_rows_inconsistent")
        for row in rows:
            route = normalize_route(row, city)
            if route["id"] in ids:
                raise CatalogFailure("route_identity_conflict")
            ids.add(route["id"])
            routes.append(route)
        retrieved = max(retrieved or "", response["retrieved_at"])
        proofs.append({"page_no": page, "raw_sha256": response["raw_sha256"], "raw_bytes": response["raw_bytes"], "retrieved_at": response["retrieved_at"]})
        if len(routes) == total:
            break
    if len(routes) != expected:
        raise CatalogFailure("incomplete_route_pages")
    routes.sort(key=lambda row: (row["route_no"], row["route_id"]))
    return {"schema_version": SCHEMA, **city, "retrieved_at": retrieved, "location_service_supported": city["city_code"] in location_codes,
            "source_page": SOURCE, "routes": routes, "pages": proofs}


def publish_catalog(cities: list[dict], results: dict[str, dict], failures: dict[str, str], location_codes: set[str],
                    *, public: Path, output: Path, client: OfficialClient, all_city_count: int) -> dict:
    payloads = {code: canonical(value) for code, value in results.items()}
    entries = []
    for city in cities:
        code = city["city_code"]
        entry = {**city, "location_service_supported": code in location_codes}
        if code in payloads:
            data = payloads[code]
            digest = sha256(data).hexdigest()
            entry.update(status="complete", route_count=len(results[code]["routes"]), file=f"{code}.json",
                         sha256=digest, byte_length=len(data), retrieved_at=results[code]["retrieved_at"])
        else:
            entry.update(status="failed", route_count=0, error_code=failures.get(code, "not_collected"))
        entries.append(entry)
    base = {"schema_version": SCHEMA, "source_id": "tago", "source_page": SOURCE, "location_source_page": LOCATION_SOURCE,
            "license": "이용허락범위 제한 없음", "kind": "route-catalog", "scope": "all-provider-cities" if len(cities) == all_city_count else "selected-provider-cities",
            "provider_city_count": all_city_count, "location_provider_city_count": len(location_codes), "selected_city_count": len(cities),
            "complete_city_count": len(results), "failed_city_count": len(failures), "route_count": sum(len(value["routes"]) for value in results.values()),
            "location_snapshots_collected": False, "cities": entries}
    version = sha256(canonical(base)).hexdigest()[:20]
    prefix = f"/data/live-transit/routes/{version}"
    for entry in entries:
        if "file" in entry:
            name = entry.pop("file")
            entry["url"] = f"{prefix}/{name}"
            atomic_new(public / entry["url"].lstrip("/"), payloads[entry["city_code"]])
    manifest = canonical(base)
    url = f"{prefix}/manifest.json"
    atomic_new(public / url.lstrip("/"), manifest)
    descriptor = {"schema_version": SCHEMA, "url": url, "sha256": sha256(manifest).hexdigest(), "byte_length": len(manifest),
                  "file_count": len(payloads) + 1, "total_byte_length": len(manifest) + sum(map(len, payloads.values())),
                  "complete_city_count": len(results), "failed_city_count": len(failures), "route_count": base["route_count"]}
    proof = {**descriptor, "checkpoint": str(client.checkpoint), "requests_started_total": client.requests_started, "cache_hits": client.cache_hits,
             "max_requests": client.max_requests, "retries": client.retries, "concurrency": 2, "completed_at": utc_now()}
    atomic_new(output / f"catalog-descriptor-{version}.json", canonical(descriptor))
    proof_bytes = canonical(proof)
    proof_path = output / f"catalog-proof-{version}-{sha256(proof_bytes).hexdigest()[:12]}.json"
    atomic_new(proof_path, proof_bytes)
    return {**descriptor, "descriptor_path": str(output / f"catalog-descriptor-{version}.json"), "proof_path": str(proof_path)}


def load_key(env_file: Path) -> str:
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("DATA_GO_KR_SERVICE_KEY="):
                key = line.split("=", 1)[1].strip()
                if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
                    key = key[1:-1]
                break
    return key


def run(args: argparse.Namespace) -> dict:
    client = OfficialClient(load_key(args.env_file), args.checkpoint, max_requests=args.max_requests, retries=1)
    cities = normalize_cities(client.request("routes", "getCtyCodeList")["payload"])
    location_codes = {city["city_code"] for city in normalize_cities(client.request("locations", "getCtyCodeList")["payload"])}
    all_city_count = len(cities)
    if args.cities:
        chosen = set(args.cities.split(","))
        if not chosen <= {city["city_code"] for city in cities}:
            raise CatalogFailure("unknown_city_selection")
        cities = [city for city in cities if city["city_code"] in chosen]
    results, failures = {}, {}
    print(json.dumps({"event": "start", "cities": len(cities), "location_cities": len(location_codes), "prior_requests": client.requests_started}, ensure_ascii=False), flush=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = {executor.submit(collect_city, client, city, location_codes): city for city in cities}
        for future in as_completed(pending):
            city = pending[future]
            try:
                result = future.result()
                results[city["city_code"]] = result
                status = {"status": "complete", "routes": len(result["routes"])}
            except CatalogFailure as error:
                failures[city["city_code"]] = error.code
                status = {"status": "failed", "error_code": error.code}
            except Exception:
                failures[city["city_code"]] = "internal_error"
                status = {"status": "failed", "error_code": "internal_error"}
            print(json.dumps({"event": "city", "city_code": city["city_code"], **status, "done": len(results) + len(failures), "total": len(cities)}, ensure_ascii=False), flush=True)
    return publish_catalog(cities, results, failures, location_codes, public=args.public, output=args.output, client=client, all_city_count=all_city_count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=ROOT / ".local/live-transit/20260917")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/live-transit")
    parser.add_argument("--public", type=Path, default=ROOT / "public")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".dev.vars")
    parser.add_argument("--max-requests", type=int, default=500)
    parser.add_argument("--cities", help="Optional comma-separated provider city codes; omitted means all returned cities")
    args = parser.parse_args()
    try:
        result = run(args)
        print(json.dumps({"event": "published", **result}, ensure_ascii=False), flush=True)
    except CatalogFailure as error:
        print(json.dumps({"event": "stopped", "error_code": error.code}), flush=True)
        raise SystemExit(1) from None
    except Exception:
        # A requests exception may contain a credential-bearing URL: never emit
        # exception text, traceback, or full request parameters from this CLI.
        print(json.dumps({"event": "stopped", "error_code": "internal_error"}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
