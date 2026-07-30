"""LLM 响应缓存层 —— 避免重复调用、降低开发成本和延迟

设计用途: 
- 开发调试阶段: 同一 prompt + 同一变量 -> 返回缓存结果，不消耗 API 额度
- 生产环境: 可选启用/禁用，通过环境变量 LLM_CACHE_ENABLED 控制

缓存策略: 
- Key = hash(provider + model + prompt_name + variables_json)
- 存储: 本地 SQLite (lmdb 风格，但用 sqlite3 内置库) 
- TTL: 默认 24 小时 (可配置) 
- 缓存命中时记录日志，便于审计

PDF 未提及，属于工程化补充。
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", "data/cache"))
CACHE_ENABLED = os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true"
DEFAULT_TTL_SECONDS = 86400  # 24 小时


class LLMCache:
    """LLM 响应的持久化缓存"""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.ttl = ttl_seconds
        self._db_path = CACHE_DIR / "llm_cache.db"
        self._ensure_db()

    def _ensure_db(self):
        """确保缓存目录和数据库文件存在"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                cache_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_name TEXT NOT NULL,
                variables_hash TEXT NOT NULL,
                response TEXT NOT NULL,
                created_at REAL NOT NULL,
                hit_count INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cache_age ON cache(created_at)
        """)
        conn.commit()
        conn.close()

    def _make_key(self, provider: str, model: str, prompt_name: str, variables: dict) -> str:
        """生成缓存键"""
        raw = json.dumps({
            "provider": provider,
            "model": model,
            "prompt_name": prompt_name,
            "variables": variables,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, provider: str, model: str, prompt_name: str, variables: dict) -> Optional[str]:
        """获取缓存 (未过期且匹配则返回，否则返回 None) """
        if not CACHE_ENABLED:
            return None

        key = self._make_key(provider, model, prompt_name, variables)
        conn = sqlite3.connect(str(self._db_path))

        row = conn.execute(
            "SELECT response, created_at FROM cache WHERE cache_key = ?",
            (key,),
        ).fetchone()

        if row is None:
            conn.close()
            return None

        response, created_at = row
        age = time.time() - created_at

        if age > self.ttl:
            # 过期删除
            conn.execute("DELETE FROM cache WHERE cache_key = ?", (key,))
            conn.commit()
            conn.close()
            return None

        # 更新命中计数
        conn.execute(
            "UPDATE cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
            (key,),
        )
        conn.commit()
        conn.close()

        logger.info(f"LLM 缓存命中: {prompt_name} (节省一次 API 调用) ")
        return response

    def set(self, provider: str, model: str, prompt_name: str, variables: dict, response: str):
        """写入缓存"""
        if not CACHE_ENABLED:
            return

        key = self._make_key(provider, model, prompt_name, variables)
        conn = sqlite3.connect(str(self._db_path))

        conn.execute(
            """INSERT OR REPLACE INTO cache
               (cache_key, provider, model, prompt_name, variables_hash, response, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                key,
                provider,
                model,
                prompt_name,
                hashlib.md5(json.dumps(variables, sort_keys=True).encode()).hexdigest(),
                response,
                time.time(),
            ),
        )
        conn.commit()
        conn.close()

    def stats(self) -> dict:
        """获取缓存统计"""
        conn = sqlite3.connect(str(self._db_path))
        total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        total_hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) FROM cache").fetchone()[0]
        expired = conn.execute(
            "SELECT COUNT(*) FROM cache WHERE created_at < ?",
            (time.time() - self.ttl,),
        ).fetchone()[0]
        conn.close()
        return {"total_entries": total, "total_hits": total_hits, "expired": expired}

    def clear_expired(self) -> int:
        """清理过期缓存"""
        conn = sqlite3.connect(str(self._db_path))
        cursor = conn.execute(
            "DELETE FROM cache WHERE created_at < ?",
            (time.time() - self.ttl,),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted:
            logger.info(f"清理了 {deleted} 条过期 LLM 缓存")
        return deleted
