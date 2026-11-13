"""Trusted query, independent corroboration and duplicate-action contracts."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from api.domain.experiments.metrics import AzureGuardrailMetrics, COLUMNS, summarize

TENANT = UUID("12345678-1234-4234-8234-123456789abc")
WORKSPACE = UUID("22345678-1234-4234-8234-123456789abc")
SCOPE = dict(tenant=TENANT, audience="hyperme.sh", experiment="signup", variant="join", metric="error_rate", start=100, end=200)


def row(source="server", value=1.0, action=None):
    return [str(action or uuid4()), str(TENANT), "hyperme.sh", "signup", "join", "error_rate", 199, value, source]


def response(rows):
    return {"tables": [{"name": "PrimaryResult", "columns": [
        {"name": name, "type": "long" if name == "ObservedAt" else "real" if name == "Value" else "string"} for name in COLUMNS], "rows": rows}]}


def test_both_authenticated_sources_must_corroborate_and_duplicates_do_not_add_samples():
    server, platform = row(), row("platform")
    assert summarize([server] * 1000, **SCOPE) is None
    result = summarize([server] * 1000 + [platform] * 1000, **SCOPE)
    assert result.samples == 1
    assert result.value == 1
    result = summarize([server, row("platform", 0.0)], **SCOPE)
    assert result.value == 0
    assert summarize([], **SCOPE) is None


def test_untrusted_mismatched_and_conflicting_rows_cannot_authorize_disable():
    base = row()
    for index, value in [(1, str(uuid4())), (2, "portal"), (3, "other"), (4, "control"), (5, "latency_ms"),
                         (6, 200), (7, 2.0), (8, "browser")]:
        changed = base.copy(); changed[index] = value
        with pytest.raises(ValueError):
            summarize([changed, row("platform")], **SCOPE)
    changed = base.copy(); changed[7] = 0.0
    with pytest.raises(ValueError, match="Conflicting"):
        summarize([base, changed, row("platform")], **SCOPE)
    with pytest.raises(ValueError, match="limit"):
        summarize([base] * 10001, **SCOPE)


@pytest.mark.asyncio
async def test_query_is_fixed_https_authenticated_scoped_and_rejects_partial_results():
    replies = [response([row(), row("platform")]), {**response([row()]), "error": {"code": "PartialError"}},
               {"tables": []}, response([])]
    async def handler(request):
        assert str(request.url) == f"https://api.loganalytics.azure.com/v1/workspaces/{WORKSPACE}/query"
        assert request.headers["authorization"] == "Bearer private-token"
        body = json.loads(request.content)
        assert set(body) == {"query"}
        assert 'ExperimentGuardrail_CL' in body["query"]
        assert f'Tenant == "{TENANT}"' in body["query"]
        assert 'Audience == "hyperme.sh"' in body["query"]
        assert 'take 10001' in body["query"]
        return httpx.Response(200, json=replies.pop(0))
    credential = SimpleNamespace(get_token=AsyncMock(return_value=SimpleNamespace(token="private-token")))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = AzureGuardrailMetrics(WORKSPACE, TENANT, "hyperme.sh", credential, client)
        assert (await source(**SCOPE)).samples == 1
        for _ in range(2):
            with pytest.raises(ValueError, match="Incomplete"):
                await source(**SCOPE)
        assert await source(**SCOPE) is None
        credential.get_token.assert_awaited_with("https://api.loganalytics.io/.default")
        with pytest.raises(ValueError):
            await source(**{**SCOPE, "tenant": uuid4()})
        with pytest.raises(ValueError):
            await source(**{**SCOPE, "experiment": 'x" | take 100'})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 429, 500])
async def test_redirects_and_provider_failures_never_supply_metrics(status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, headers={"location": "https://foreign.test/"}))) as client:
        source = AzureGuardrailMetrics(WORKSPACE, TENANT, "hyperme.sh", SimpleNamespace(get_token=AsyncMock(return_value=SimpleNamespace(token="secret"))), client)
        with pytest.raises(ValueError, match="unavailable"):
            await source(**SCOPE)
