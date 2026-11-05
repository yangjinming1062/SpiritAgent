"""账户检索向量的单飞补齐与运行生命周期。"""

import asyncio

from infrastructure.ai.llm_client import embedding_model_key, generate_embeddings, resolve_embedding_provider
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from infrastructure.search import QueryEmbedding, valid_vectors
from infrastructure.search_index import SearchIndexRow
from sqlalchemy import or_, select, update

from .background import TaskScope
from .lifecycle import RuntimeParticipant
from .user_maintenance import is_user_in_maintenance

logger = get_logger(__name__)


class SearchIndexWorker:
    def __init__(self, name: str, model: type[SearchIndexRow]) -> None:
        self._name = name
        self._model = model
        self._targets: dict[int, tuple[str, int]] = {}
        self._requests: set[int] = set()
        self._bag = TaskScope(name)

    def schedule(self, user_id: int, vector: QueryEmbedding | None = None) -> None:
        if is_user_in_maintenance(user_id):
            return
        if vector is not None:
            self._targets[user_id] = (vector.model_key, len(vector.vector))
        self._requests.add(user_id)
        self._bag.spawn(user_id, lambda: self._rebuild(user_id), name=f"{self._name}-{user_id}", user_id=user_id)

    def invalidate(self, user_id: int) -> None:
        self._targets.pop(user_id, None)
        self._requests.discard(user_id)
        if (task := self._bag.running(user_id)) is not None:
            task.cancel()

    async def _rebuild(self, user_id: int) -> None:
        model = self._model
        try:
            while not is_user_in_maintenance(user_id):
                self._requests.discard(user_id)
                async with session_scope() as db:
                    provider = await resolve_embedding_provider(db, user_id)
                    if provider is None:
                        return
                    key = embedding_model_key(provider.config)
                    target = self._targets.get(user_id)
                    dimensions = target[1] if target is not None and target[0] == key else None
                    stale = [
                        model.embedding.is_(None),
                        model.model_key != key,
                        model.embedded_hash != model.content_hash,
                    ]
                    if dimensions is not None:
                        stale.append(model.dimensions != dimensions)
                    rows = list(
                        await db.execute(
                            select(model.id, model.search_text, model.content_hash)
                            .where(model.user_id == user_id, or_(*stale))
                            .order_by(model.id)
                            .limit(100)
                        )
                    )
                if not rows:
                    if user_id in self._requests:
                        continue
                    return
                vectors = await generate_embeddings([row.search_text for row in rows], provider, user_id=user_id)
                if (
                    vectors is None
                    or not valid_vectors(vectors, len(rows), dimensions)
                    or is_user_in_maintenance(user_id)
                ):
                    return
                async with session_scope() as db:
                    for row, vector in zip(rows, vectors, strict=True):
                        await db.execute(
                            update(model)
                            .where(model.user_id == user_id, model.id == row.id, model.content_hash == row.content_hash)
                            .values(
                                embedding=vector, model_key=key, dimensions=len(vector), embedded_hash=row.content_hash
                            )
                        )
                    await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "search embedding rebuild failed", extra={"owner": self._name, "user_id": user_id}, exc_info=True
            )
        finally:
            self._requests.discard(user_id)

    def participant(self) -> RuntimeParticipant:
        return RuntimeParticipant(self._name, invalidate_user=self.invalidate, drain=self._bag.drain)
