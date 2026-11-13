"""Authenticated, bounded server/platform observations for disable decisions."""
import json
from collections import defaultdict
from typing import Literal
from uuid import UUID

import httpx
from pydantic import Field, model_validator

from api.domain.experiments.control import Aggregate, ClosedModel, Label, Metric, Second

TABLE = "ExperimentGuardrail_CL"
COLUMNS = ("ActionId", "Tenant", "Audience", "Experiment", "Variant", "Metric", "ObservedAt", "Value", "Source")
MAX_ROWS = 10000


class Observation(ClosedModel):
    action: UUID
    tenant: UUID
    audience: Literal["s3r.ch", "hyperme.sh", "portal"]
    experiment: Label
    variant: Label
    metric: Metric
    observed: Second
    value: float = Field(ge=0, allow_inf_nan=False)
    source: Literal["server", "platform"]

    @model_validator(mode="after")
    def bounded(self):
        if self.action.version != 4 or not self.tenant.int or self.metric != "latency_ms" and self.value not in (0.0, 1.0):
            raise ValueError("Invalid guardrail observation")
        return self


def summarize(rows, *, tenant, audience, experiment, variant, metric, start, end):
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("Guardrail query exceeds limit")
    unique = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != len(COLUMNS):
            raise ValueError("Invalid guardrail row")
        sample = Observation.model_validate_json(json.dumps(dict(zip(
            ("action", "tenant", "audience", "experiment", "variant", "metric", "observed", "value", "source"), row))))
        if ((sample.tenant, sample.audience, sample.experiment, sample.variant, sample.metric) !=
                (tenant, audience, experiment, variant, metric) or not start <= sample.observed < end):
            raise ValueError("Guardrail query scope mismatch")
        key = (sample.source, sample.action)
        if key in unique and unique[key] != sample:
            raise ValueError("Conflicting guardrail action")
        unique[key] = sample
    groups = defaultdict(list)
    for sample in unique.values():
        groups[sample.source].append(sample)
    if set(groups) != {"server", "platform"}:
        return None
    # Independent ingestion principals corroborate the regression. A browser
    # report, or either observer alone, cannot satisfy this decision contract.
    if any(max(s.observed for s in group) < end - min(300, end - start) for group in groups.values()):
        return None
    return Aggregate(tenant=tenant, audience=audience, experiment=experiment, variant=variant, metric=metric,
        start=start, end=end, samples=min(len(group) for group in groups.values()),
        value=min(sum(s.value for s in group) / len(group) for group in groups.values()))


class AzureGuardrailMetrics:
    """Read only the dedicated DCR table using a deployment-owned credential.

    DCR transformations bind tenant, audience and source; their publisher roles
    are never issued to browsers or the ordinary analytics event path.
    """
    def __init__(self, workspace: UUID, tenant: UUID, audience: str, credential, client: httpx.AsyncClient):
        if not workspace.int or not tenant.int or audience not in ("s3r.ch", "hyperme.sh", "portal"):
            raise ValueError("Explicit guardrail binding required")
        self.workspace, self.tenant, self.audience = workspace, tenant, audience
        self.credential, self.client = credential, client

    async def __call__(self, tenant, audience, experiment, variant, metric, start, end):
        # Reuse the closed model to validate every interpolated query value.
        scope = Aggregate(tenant=tenant, audience=audience, experiment=experiment, variant=variant,
            metric=metric, start=start, end=end, samples=0, value=0.0)
        if tenant != self.tenant or audience != self.audience or not 60 <= end - start <= 86400:
            raise ValueError("Guardrail query scope mismatch")
        query = (f'{TABLE} | where Tenant == "{tenant}" and Audience == "{audience}" '
            f'and Experiment == "{scope.experiment}" and Variant == "{scope.variant}" and Metric == "{metric}" '
            f'and ObservedAt >= {start} and ObservedAt < {end} '
            f'| project {", ".join(COLUMNS)} | take {MAX_ROWS + 1}')
        token = await self.credential.get_token("https://api.loganalytics.io/.default")
        if not isinstance(token.token, str) or not token.token or len(token.token) > 16384:
            raise ValueError("Guardrail credential unavailable")
        async with self.client.stream("POST", f"https://api.loganalytics.azure.com/v1/workspaces/{self.workspace}/query",
                headers={"Authorization": f"Bearer {token.token}", "Prefer": "wait=10", "Content-Type": "application/json"},
                json={"query": query}, follow_redirects=False, timeout=12) as response:
            if response.status_code != 200:
                raise ValueError("Guardrail query unavailable")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 4 * 1024 * 1024:
                    raise ValueError("Guardrail query exceeds limit")
        result = json.loads(data)
        # Partial results never authorize a decision, even with HTTP 200.
        if not isinstance(result, dict) or set(result) != {"tables"} or not isinstance(result["tables"], list) or len(result["tables"]) != 1:
            raise ValueError("Incomplete guardrail query")
        table = result["tables"][0]
        if not isinstance(table, dict) or table.get("name") != "PrimaryResult" or table.get("columns") != [
            {"name": name, "type": "long" if name == "ObservedAt" else "real" if name == "Value" else "string"} for name in COLUMNS]:
            raise ValueError("Invalid guardrail query schema")
        return summarize(table.get("rows"), tenant=tenant, audience=audience, experiment=experiment,
            variant=variant, metric=metric, start=start, end=end)
