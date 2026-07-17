from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_PATH = (
    REPOSITORY_ROOT
    / "postman"
    / "AutoTask_RPA_Engine_v0.5.0.postman_collection.json"
)

POSTMAN_V2_1_SCHEMA = (
    "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
)

EXPECTED_ROUTES = {
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/v1/flows"),
    ("POST", "/api/v1/flows/packages"),
    ("GET", "/api/v1/flows/{rpa_flow_id}"),
    ("GET", "/api/v1/flows/{rpa_flow_id}/versions"),
    ("POST", "/api/v1/flows/{rpa_flow_id}/disable"),
    ("POST", "/api/v1/flows/{rpa_flow_id}/rollback"),
    ("POST", "/api/v1/flow-versions/validate-binding"),
    ("GET", "/api/v1/flow-versions/{flow_version_id}"),
    ("POST", "/api/v1/flow-versions/{flow_version_id}/validate"),
    ("POST", "/api/v1/flow-versions/{flow_version_id}/publish"),
    ("POST", "/api/v1/flow-versions/{flow_version_id}/deprecate"),
    ("POST", "/api/v1/flow-versions/{flow_version_id}/disable"),
    ("GET", "/api/v1/flow-versions/{flow_version_id}/package"),
    ("GET", "/api/v1/workers"),
    ("GET", "/api/v1/workers/{worker_id}"),
}

REGISTRY_WRITE_ROUTES = {
    ("POST", "/api/v1/flows/packages"),
    ("POST", "/api/v1/flow-versions/{flow_version_id}/validate"),
    ("POST", "/api/v1/flow-versions/{flow_version_id}/publish"),
}

LIFECYCLE_ROUTES = {
    ("POST", "/api/v1/flows/{rpa_flow_id}/disable"),
    ("POST", "/api/v1/flows/{rpa_flow_id}/rollback"),
    ("POST", "/api/v1/flow-versions/{flow_version_id}/deprecate"),
    ("POST", "/api/v1/flow-versions/{flow_version_id}/disable"),
}

REQUIRED_VARIABLES = {
    "engine_base_url",
    "actor_id",
    "tenant_id",
    "flow_scope",
    "flow_id",
    "flow_version",
    "flow_version_id",
    "target_flow_version_id",
    "workflow_code",
    "package_checksum",
    "worker_id",
    "worker_status",
    "capability",
    "limit",
    "offset",
    "change_reason",
    "allow_registry_writes",
    "allow_lifecycle_changes",
}

PRIVATE_IPV4 = re.compile(
    r"(?<!\d)(?:10(?:\.\d{1,3}){3}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?!\d)"
)
JWT_VALUE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.")
SIGNED_URL_MARKERS = (
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "awsaccesskeyid",
    "googleaccessid",
    "signature=",
)


@dataclass(frozen=True)
class RequestRecord:
    item: Mapping[str, Any]
    request: Mapping[str, Any]
    method: str
    path: str
    prerequest_scripts: tuple[str, ...]

    @property
    def route(self) -> tuple[str, str]:
        return self.method, self.path


def _load_collection() -> dict[str, Any]:
    assert COLLECTION_PATH.is_file(), f"Postman collection missing: {COLLECTION_PATH}"
    loaded = json.loads(COLLECTION_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _scripts(
    owner: Mapping[str, Any],
    *,
    listen: str,
) -> tuple[str, ...]:
    scripts: list[str] = []
    for event in owner.get("event", []):
        if not isinstance(event, Mapping) or event.get("listen") != listen:
            continue
        script = event.get("script", {})
        if not isinstance(script, Mapping):
            continue
        lines = script.get("exec", [])
        if isinstance(lines, str):
            scripts.append(lines)
        elif isinstance(lines, Sequence):
            scripts.append("\n".join(str(line) for line in lines))
    return tuple(scripts)


def _raw_url(request: Mapping[str, Any]) -> str:
    url = request.get("url", "")
    if isinstance(url, str):
        return url
    if not isinstance(url, Mapping):
        return ""
    raw = url.get("raw")
    if isinstance(raw, str):
        return raw
    path = url.get("path", [])
    if isinstance(path, Sequence) and not isinstance(path, str):
        return "/" + "/".join(str(segment) for segment in path)
    return ""


def _normalized_path(request: Mapping[str, Any]) -> str:
    raw = _raw_url(request).split("?", maxsplit=1)[0]
    raw = raw.removeprefix("{{engine_base_url}}")
    replacements = {
        "{{flow_id}}": "{rpa_flow_id}",
        "{{flow_version_id}}": "{flow_version_id}",
        "{{worker_id}}": "{worker_id}",
    }
    for variable, route_parameter in replacements.items():
        raw = raw.replace(variable, route_parameter)
    return "/" + raw.lstrip("/")


def _iter_requests(
    items: Sequence[Any],
    inherited_scripts: tuple[str, ...] = (),
) -> Iterator[RequestRecord]:
    for candidate in items:
        if not isinstance(candidate, Mapping):
            continue
        scripts = inherited_scripts + _scripts(candidate, listen="prerequest")
        request = candidate.get("request")
        if isinstance(request, Mapping):
            yield RequestRecord(
                item=candidate,
                request=request,
                method=str(request.get("method", "")).upper(),
                path=_normalized_path(request),
                prerequest_scripts=scripts,
            )
        children = candidate.get("item")
        if isinstance(children, Sequence) and not isinstance(children, str):
            yield from _iter_requests(children, scripts)


def _request_records(collection: Mapping[str, Any]) -> list[RequestRecord]:
    items = collection.get("item", [])
    assert isinstance(items, Sequence) and not isinstance(items, str)
    collection_scripts = _scripts(collection, listen="prerequest")
    return list(_iter_requests(items, collection_scripts))


def _variables(collection: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for variable in collection.get("variable", []):
        if isinstance(variable, Mapping) and isinstance(variable.get("key"), str):
            result[variable["key"]] = variable.get("value")
    return result


def _headers(request: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    for header in request.get("header", []):
        if not isinstance(header, Mapping):
            continue
        yield str(header.get("key", "")), str(header.get("value", ""))


def _assert_blocking_guard(record: RequestRecord, variable: str) -> None:
    script = "\n".join(_scripts(record.item, listen="prerequest"))
    assert variable in script, f"{record.route} is missing the {variable} guard"
    assert re.search(
        r"(?:throw\s+new\s+Error|pm\.execution\.skipRequest\s*\(\s*\))",
        script,
    ), (
        f"{record.route} does not stop execution when {variable} is disabled"
    )


def test_collection_is_postman_v2_1_json() -> None:
    collection = _load_collection()

    assert collection.get("info", {}).get("schema") == POSTMAN_V2_1_SCHEMA
    assert isinstance(collection.get("item"), list)


def test_collection_covers_each_engine_route_exactly_once() -> None:
    records = _request_records(_load_collection())
    routes = Counter(record.route for record in records)

    assert set(routes) == EXPECTED_ROUTES
    assert all(count == 1 for count in routes.values())
    assert len(records) == len(EXPECTED_ROUTES) == 17

    for record in records:
        lower_url = _raw_url(record.request).lower()
        assert "/api/v1/autotask" not in lower_url
        assert "worker-api" not in lower_url
        assert not re.search(r"/(?:run|runs)(?:/|$|\?)", lower_url)


def test_collection_variables_are_safe_and_complete() -> None:
    collection = _load_collection()
    variables = _variables(collection)

    assert REQUIRED_VARIABLES <= variables.keys()
    assert variables["engine_base_url"] == "http://localhost:4610"
    assert variables["tenant_id"] == ""
    assert variables["flow_scope"] == "GLOBAL"
    assert variables["flow_id"] == "rpa_flow_mock_srm_fetch_po"
    assert variables["flow_version"] == "1.1.0"
    assert variables["workflow_code"] == "srm_fetch_po"
    assert variables["allow_registry_writes"] in (False, "false")
    assert variables["allow_lifecycle_changes"] in (False, "false")
    assert str(variables["actor_id"]).strip()
    assert str(variables["change_reason"]).strip()

    serialized = json.dumps(collection, ensure_ascii=False)
    assert PRIVATE_IPV4.search(serialized) is None
    assert JWT_VALUE.search(serialized) is None
    assert not any(marker in serialized.lower() for marker in SIGNED_URL_MARKERS)

    for key, value in variables.items():
        assert not re.search(
            r"authorization|jwt|password|secret|(?:access_?)?token",
            key,
            re.IGNORECASE,
        )
        assert JWT_VALUE.search(str(value)) is None

    auth = collection.get("auth")
    assert auth is None or auth == {"type": "noauth"}
    for record in _request_records(collection):
        request_auth = record.request.get("auth")
        assert request_auth is None or request_auth == {"type": "noauth"}
        for key, value in _headers(record.request):
            assert key.lower() != "authorization"
            assert not value.lower().startswith("bearer ")
        request_data = json.dumps(record.request, ensure_ascii=False).lower()
        assert '"password"' not in request_data
        assert '"secret"' not in request_data
        assert '"jwt"' not in request_data


def test_collection_level_headers_are_added_only_for_engine_api() -> None:
    collection = _load_collection()
    scripts = _scripts(collection, listen="prerequest")
    assert scripts, "A collection-level prerequest script is required"
    script = "\n".join(scripts)

    assert "actor_id" in script
    assert "tenant_id" in script
    assert "X-Actor-Id" in script
    assert "X-Tenant-Id" in script
    assert re.search(r"headers\.(?:upsert|add)\s*\(", script)
    assert re.search(
        r"headers\.remove\s*\(\s*['\"]X-Tenant-Id['\"]\s*\)",
        script,
    )


def test_mutating_requests_have_explicit_opt_in_guards() -> None:
    records = {
        record.route: record for record in _request_records(_load_collection())
    }

    for route in REGISTRY_WRITE_ROUTES:
        _assert_blocking_guard(records[route], "allow_registry_writes")
    for route in LIFECYCLE_ROUTES:
        _assert_blocking_guard(records[route], "allow_lifecycle_changes")


def test_package_request_does_not_follow_redirects() -> None:
    package_route = (
        "GET",
        "/api/v1/flow-versions/{flow_version_id}/package",
    )
    records = _request_records(_load_collection())
    package = next(record for record in records if record.route == package_route)
    behavior = package.item.get(
        "protocolProfileBehavior",
        package.request.get("protocolProfileBehavior", {}),
    )

    assert isinstance(behavior, Mapping)
    assert behavior.get("followRedirects") is False
