from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.live_transit_catalog import (CatalogFailure, OfficialClient, atomic_new, canonical, collect_city,
                                         normalize_cities, normalize_route, publish_catalog, body_items)


CITY = {"city_code": "12", "city_name": "세종특별시"}


def payload(rows, **body):
    return {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": rows}, **body}}}


def route(identifier="SJB293000077", **extra):
    return {"routeid": identifier, "routeno": "B2", "routetp": "간선버스", "startnodenm": "대전", "endnodenm": "오송", **extra}


class StubClient:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.requests = []

    def request(self, *args):
        self.requests.append(args)
        value = next(self.pages)
        return {"payload": value, "retrieved_at": "2026-09-16T16:23:49Z", "raw_sha256": sha256(canonical(value)).hexdigest(), "raw_bytes": len(canonical(value))}


def test_normalize_cities_requires_distinct_complete_official_ids():
    assert normalize_cities(payload([{"citycode": 12, "cityname": "세종특별시"}])) == [CITY]
    with pytest.raises(CatalogFailure, match="city_identity_conflict"):
        normalize_cities(payload([{"citycode": 12, "cityname": "세종"}, {"citycode": "12", "cityname": "세종"}]))
    with pytest.raises(CatalogFailure, match="city_pagination_incomplete"):
        normalize_cities(payload([{"citycode": 12, "cityname": "세종"}], totalCount=2))


def test_direction_ids_remain_distinct_despite_same_route_number():
    first = normalize_route(route(), CITY)
    second = normalize_route(route("SJB293000099"), CITY)
    assert first["route_no"] == second["route_no"] == "B2"
    assert first["id"] != second["id"]
    assert normalize_route(route("GMB수점10"), {"city_code": "37050", "city_name": "구미시"})["id"] == "tago-37050-gmb수점10"
    with pytest.raises(CatalogFailure, match="invalid_route_id"):
        normalize_route(route("https://external.test"), CITY)


def test_city_pages_are_complete_and_preserve_source_proof():
    rows = [route(f"SJB{i:09}") for i in range(1001)]
    client = StubClient([payload(rows[:1000], totalCount=1001, pageNo=1, numOfRows=1000), payload(rows[1000:], totalCount=1001, pageNo=2, numOfRows=1000)])
    result = collect_city(client, CITY, {"12"})
    assert len(result["routes"]) == 1001
    assert len(result["pages"]) == 2
    assert result["location_service_supported"]
    assert all(call[0:2] == ("routes", "getRouteNoList") for call in client.requests)


@pytest.mark.parametrize("second", [payload([route("new")], totalCount=1002, pageNo=2, numOfRows=1000),
                                        payload([route("SJB000000000")], totalCount=1001, pageNo=2, numOfRows=1000)])
def test_changing_totals_and_duplicate_ids_fail_city_as_a_unit(second):
    rows = [route(f"SJB{i:09}") for i in range(1000)]
    client = StubClient([payload(rows, totalCount=1001, pageNo=1, numOfRows=1000), second])
    with pytest.raises(CatalogFailure, match="page_metadata_inconsistent|route_identity_conflict"):
        collect_city(client, CITY, {"12"})


def test_missing_pages_are_not_silently_accepted():
    client = StubClient([payload([route()], totalCount=2, pageNo=1, numOfRows=1000)])
    with pytest.raises(CatalogFailure, match="page_rows_inconsistent"):
        collect_city(client, CITY, set())


def test_empty_route_city_is_complete_but_not_proof_of_location_support():
    result = collect_city(StubClient([payload([], totalCount=0, pageNo=1, numOfRows=1000)]), CITY, set())
    assert result["routes"] == []
    assert result["location_service_supported"] is False


def test_source_auth_and_quota_errors_are_fixed_and_stop_collection():
    for source_code, safe_code in [("20", "upstream_auth"), ("22", "upstream_quota")]:
        with pytest.raises(CatalogFailure) as error:
            body_items({"response": {"header": {"resultCode": source_code, "resultMsg": "private value"}}})
        assert error.value.code == safe_code
        assert error.value.stop
        assert "private" not in str(error.value)


def test_request_operation_and_budget_guard_happen_before_network(tmp_path):
    client = OfficialClient("fixture-private-key", tmp_path, max_requests=1)
    with pytest.raises(CatalogFailure, match="unapproved_operation"):
        client.request("routes", "getRouteAcctoBusLcList")
    with pytest.raises(CatalogFailure, match="invalid_request_conditions"):
        client.request("routes", "getRouteNoList", {"url": "https://external.test"})
    client.reserve("routes", "getCtyCodeList", {})
    restarted = OfficialClient("fixture-private-key", tmp_path, max_requests=1)
    with pytest.raises(CatalogFailure, match="request_budget_exhausted"):
        restarted.reserve("routes", "getCtyCodeList", {})
    assert "fixture-private-key" not in (tmp_path / "requests.jsonl").read_text()


def test_checkpoint_hash_validation_and_reuse(tmp_path):
    client = OfficialClient("fixture-private-key", tmp_path)
    identity = {"service": "routes", "operation": "getCtyCodeList", "conditions": {}}
    response = payload([{"citycode": 12, "cityname": "세종특별시"}])
    page = {"schema_version": 1, "identity": identity, "payload": response, "payload_sha256": sha256(canonical(response)).hexdigest()}
    path = tmp_path / "pages" / f"routes-getCtyCodeList-{sha256(canonical(identity)).hexdigest()[:24]}.json"
    atomic_new(path, canonical(page))
    assert client.request("routes", "getCtyCodeList")["payload"] == response
    assert client.cache_hits == 1
    assert client.requests_started == 0
    altered = {**page, "payload_sha256": "bad"}
    # Corruption is intentional test input in a temporary directory.
    path.write_bytes(canonical(altered))
    with pytest.raises(CatalogFailure, match="checkpoint_invalid"):
        client.request("routes", "getCtyCodeList")


def test_publication_keeps_failures_explicit_and_reuses_immutable_payloads(tmp_path):
    complete = collect_city(StubClient([payload([route()], totalCount=1, pageNo=1, numOfRows=1000)]), CITY, {"12"})
    missing = {"city_code": "25", "city_name": "대전"}
    client = SimpleNamespace(checkpoint=tmp_path / "checkpoint", requests_started=3, cache_hits=0, max_requests=500, retries=1)
    kwargs = dict(public=tmp_path / "public", output=tmp_path / "output", client=client, all_city_count=2)
    first = publish_catalog([CITY, missing], {"12": complete}, {"25": "upstream_timeout"}, {"12", "25"}, **kwargs)
    manifest = json.loads((kwargs["public"] / first["url"].lstrip("/")).read_bytes())
    assert manifest["route_count"] == 1
    assert manifest["complete_city_count"] == manifest["failed_city_count"] == 1
    assert manifest["location_snapshots_collected"] is False
    assert manifest["cities"][1]["status"] == "failed"
    assert "url" not in manifest["cities"][1]
    city = manifest["cities"][0]
    city_bytes = (kwargs["public"] / city["url"].lstrip("/")).read_bytes()
    assert len(city_bytes) == city["byte_length"]
    assert sha256(city_bytes).hexdigest() == city["sha256"]
    second = publish_catalog([CITY, missing], {"12": complete}, {"25": "upstream_timeout"}, {"12", "25"}, **kwargs)
    assert first["url"] == second["url"]


def test_immutable_writer_never_overwrites_conflicting_original(tmp_path):
    path = tmp_path / "original.json"
    atomic_new(path, b"original")
    with pytest.raises(CatalogFailure, match="immutable_file_conflict"):
        atomic_new(path, b"changed")
    assert path.read_bytes() == b"original"


class NetworkResponse:
    def __init__(self, data, status=200, headers=None):
        self.status_code, self.data, self.headers = status, data, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_content(self, _size):
        yield self.data


class NetworkSession:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_actual_request_saves_a_valid_checkpoint_without_authenticated_url(tmp_path):
    data = canonical(payload([{"citycode": 12, "cityname": "세종특별시"}]))
    session = NetworkSession(NetworkResponse(data))
    client = OfficialClient("fixture-private-key", tmp_path, session_factory=lambda: session)
    result = client.request("routes", "getCtyCodeList")
    assert result["raw_sha256"] == sha256(data).hexdigest()
    assert session.calls[0][0].startswith("https://apis.data.go.kr/")
    assert session.calls[0][1]["allow_redirects"] is False
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert "fixture-private-key" not in path.read_text(encoding="utf-8")
    client.request("routes", "getCtyCodeList")
    assert len(session.calls) == 1


@pytest.mark.parametrize("status,body,error", [(401, b"private source body", "upstream_auth"),
                                             (429, b"private source body", "upstream_quota"),
                                             (200, b'{"key":"fixture-private-key"}', "reflected_credential")])
def test_sensitive_auth_or_quota_failure_stops_all_further_work(tmp_path, status, body, error):
    session = NetworkSession(NetworkResponse(body, status=status))
    client = OfficialClient("fixture-private-key", tmp_path, session_factory=lambda: session)
    with pytest.raises(CatalogFailure, match=error):
        client.request("routes", "getCtyCodeList")
    with pytest.raises(CatalogFailure, match="collection_stopped"):
        client.request("locations", "getCtyCodeList")
    assert len(session.calls) == 1
    assert not (tmp_path / "pages").exists()


def test_network_retry_count_is_bounded_and_counted_in_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.live_transit_catalog.time.sleep", lambda _delay: None)
    session = NetworkSession(NetworkResponse(b"temporary error", status=503))
    client = OfficialClient("fixture-private-key", tmp_path, session_factory=lambda: session)
    with pytest.raises(CatalogFailure, match="upstream_http"):
        client.request("routes", "getCtyCodeList")
    assert len(session.calls) == client.requests_started == 2
