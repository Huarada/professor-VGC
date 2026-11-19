"""Cloud Run conversation memory scoped to one anonymous browser session."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from google.cloud import firestore

from src.domain.models import ChatMessage


class FirestoreConversationMemory:
    def __init__(self, project: str, collection: str = "web_conversations") -> None:
        self.db: Any = firestore.Client(project=project)
        self.collection = self.db.collection(collection)

    def load(self, session_id: str) -> list[ChatMessage]:
        now = datetime.now(timezone.utc)
        records = self.collection.document(session_id).collection("messages").order_by(
            "__name__", direction=firestore.Query.DESCENDING).limit(50).stream()
        messages = [row.to_dict() for row in records]
        return [ChatMessage(role=row["role"], content=row["content"])
                for row in reversed(messages) if row["expires_at"] > now]

    def append(self, session_id: str, message: ChatMessage) -> None:
        root = self.collection.document(session_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def insert(tx: Any) -> None:
            snapshot: Any = root.get(transaction=tx)
            data = (snapshot.to_dict() or {}) if snapshot.exists else {}
            number = int(data.get("next", 0)) + 1
            tx.set(root, {"next": number}, merge=True)
            tx.create(root.collection("messages").document(f"{number:012d}"),
                      {"role": message.role, "content": message.content,
                       "expires_at": datetime.now(timezone.utc) + timedelta(days=1)})

        insert(transaction)

    def clear(self, session_id: str) -> None:
        root = self.collection.document(session_id)
        while True:
            records = list(root.collection("messages").limit(400).stream())
            if not records:
                break
            batch = self.db.batch()
            for record in records:
                batch.delete(record.reference)
            batch.commit()
        root.delete()
