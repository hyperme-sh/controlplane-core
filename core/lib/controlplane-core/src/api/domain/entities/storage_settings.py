from enum import Enum

from controlplane_db.models import StorageSettings


class StorageProvider(str, Enum):
    AWS_S3 = "s3"
    AZURE_BLOB = "azure_blob"


__all__ = ["StorageSettings", "StorageProvider"]
