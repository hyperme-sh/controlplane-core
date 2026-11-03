from pydantic import BaseModel
from datetime import datetime

from controlplane_db.models import ChatHistoryAI, ChatSessionAI


class History(BaseModel):
    uid: str
    query: str
    response: str
    timestamp: datetime

    def to_serializable_dict(self):
        return {
            "uid": str(self.uid),
            "query": self.query,
            "response": self.response,
            "timestamp": str(self.timestamp),
        }


__all__ = ["ChatHistoryAI", "ChatSessionAI", "History"]
