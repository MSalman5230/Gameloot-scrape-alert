from stockwatch.config import Config
from stockwatch.storage.base import Store
from stockwatch.storage.memory import MemoryStore

__all__ = ["MemoryStore", "Store", "open_store"]


async def open_store(config: Config) -> Store:
    if config.mongodb_uri.startswith("memory://"):
        return MemoryStore()

    from stockwatch.storage.migrate import migrate_legacy
    from stockwatch.storage.mongo import MongoStore

    store = await MongoStore.connect(config.mongodb_uri, config.mongodb_default_db)
    await migrate_legacy(store.db)
    return store
