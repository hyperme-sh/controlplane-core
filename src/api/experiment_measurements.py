"""Default-disabled private platform observer; separate process and credentials."""
import asyncio
import os
import stat
from pathlib import Path
from uuid import UUID

import httpx
import uvicorn
from azure.identity.aio import ClientSecretCredential

from api.domain.experiments.producers import Admission, AzurePublisher, Binding, BoundedPublisher, create_relay
from api.experiment_guardrails import private_secret


def private_directory(path):
    value = Path(path)
    info = value.lstat()
    if not value.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Private state directory required")
    return value


async def run():
    # No credentials, sockets or state accessed while disabled.
    if os.environ.get("EXPERIMENT_MEASUREMENTS_ENABLED") != "1":
        return
    binding = Binding.model_validate_json(private_secret(os.environ["EXPERIMENT_MEASUREMENTS_BINDING_FILE"]))
    token = private_secret(os.environ["EXPERIMENT_MEASUREMENTS_RELAY_SECRET_FILE"])
    directory = UUID(os.environ["EXPERIMENT_MEASUREMENTS_DIRECTORY"])
    client_id = UUID(os.environ["EXPERIMENT_MEASUREMENTS_CLIENT_ID"])
    if not directory.int or not client_id.int:
        raise ValueError("Explicit platform principal required")
    secret = private_secret(os.environ["EXPERIMENT_MEASUREMENTS_SECRET_FILE"])
    state = private_directory(os.environ["EXPERIMENT_MEASUREMENTS_STATE_DIRECTORY"])
    database = state / "actions.sqlite3"
    if database.is_symlink():
        raise ValueError("Private replay store required")
    admission = Admission(str(database))
    try:
        async with ClientSecretCredential(str(directory), str(client_id), secret) as credential, httpx.AsyncClient(trust_env=False) as client:
            publish = AzurePublisher(os.environ["EXPERIMENT_MEASUREMENTS_INGESTION_ENDPOINT"],
                                     os.environ["EXPERIMENT_MEASUREMENTS_DCR_ID"], credential, client)
            async with BoundedPublisher(publish) as delivery:
                app = create_relay(binding=binding, token=token, admission=admission, client=client, publish=delivery, enabled=True)
                # Loopback-only; deployment's private TLS listener owns external access.
                await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8097,
                    access_log=False, log_level="critical", proxy_headers=False)).serve()
    finally:
        admission.close()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit("Experiment measurement configuration unavailable") from None


if __name__ == "__main__":
    main()
