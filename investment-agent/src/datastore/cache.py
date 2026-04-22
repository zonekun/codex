"""APIレスポンスキャッシュ機構.

diskcacheを使用してAPIレスポンスをローカルにキャッシュする。
キャッシュ有効期限はsettings.yamlで設定。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.config import app_config
from src.core.logger import get_logger

log = get_logger(__name__)


class DataCache:
    """ディスクベースのデータキャッシュ."""

    def __init__(self) -> None:
        cfg = app_config.get("datastore", {})
        self.cache_dir = Path(cfg.get("cache_dir", "data/cache"))
        self.ttl_seconds = cfg.get("cache_ttl_hours", 24) * 3600
        self._cache = None

    def _get_cache(self):
        if self._cache is None:
            import diskcache
            self._cache = diskcache.Cache(str(self.cache_dir))
        return self._cache

    def get(self, key: str) -> Any | None:
        """キャッシュからデータを取得する."""
        cache = self._get_cache()
        value = cache.get(key)
        if value is not None:
            log.debug("cache_hit", key=key)
        return value

    def set(self, key: str, value: Any) -> None:
        """キャッシュにデータを保存する."""
        cache = self._get_cache()
        cache.set(key, value, expire=self.ttl_seconds)
        log.debug("cache_set", key=key, ttl=self.ttl_seconds)

    def clear(self) -> None:
        """キャッシュをクリアする."""
        cache = self._get_cache()
        cache.clear()
        log.info("cache_cleared")
