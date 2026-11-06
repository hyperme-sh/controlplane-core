"""Tenant host lookup belongs to user-service."""
import os
import uuid
import httpx

async def resolve_host(host:str) -> uuid.UUID | None:
    url=os.environ.get('USER_SERVICE_URL','').rstrip('/')
    token=os.environ.get('TENANT_OWNER_TOKEN','')
    if not url or not token:raise RuntimeError('Tenant owner is not configured')
    async with httpx.AsyncClient(timeout=5,follow_redirects=False) as client:
        response=await client.get(url+'/api/v1/internal/identity/domain',params={'host':host},
            headers={'Authorization':'Bearer '+token})
        if response.status_code==404:return None
        response.raise_for_status()
        return uuid.UUID(response.json()['tenant_id'])
