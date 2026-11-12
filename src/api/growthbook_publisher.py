"""Exclusive GrowthBook publisher entry point; imported policy never renews offline."""
import asyncio
import os
import ssl
import stat
from uuid import UUID

from api.domain.experiments.growthbook import GrowthBookSource
from api.experiment_publisher import run


def read_secret(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or not 1 <= info.st_size <= 512):
            raise ValueError('Private GrowthBook credential file required')
        return os.read(fd,513).decode('ascii').strip()
    finally:
        os.close(fd)


def binding(env):
    return GrowthBookSource(origin=env['GROWTHBOOK_API_ORIGIN'],
        organization=env['GROWTHBOOK_ORGANIZATION'],project=env['GROWTHBOOK_PROJECT'],
        environment=env['GROWTHBOOK_ENVIRONMENT'],connection=env['GROWTHBOOK_SDK_CONNECTION'],
        api_token=read_secret(env['GROWTHBOOK_READ_TOKEN_FILE']),sdk_key=read_secret(env['GROWTHBOOK_SDK_KEY_FILE']),
        tenant=UUID(env['EXPERIMENT_TENANT_ID']),audience=env['EXPERIMENT_AUDIENCE'],
        maximum_stale_seconds=int(env.get('GROWTHBOOK_MAXIMUM_STALE_SECONDS','300')))


def main():
    try:
        source=binding(os.environ)
        verify=ssl.create_default_context(cafile=os.environ.get('GROWTHBOOK_CA_FILE') or None)
        asyncio.run(run(source=source,verify=verify))
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit('GrowthBook publisher configuration unavailable') from None


if __name__=='__main__':main()
