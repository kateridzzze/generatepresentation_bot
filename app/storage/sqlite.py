"""Асинхронное хранилище на SQLite: FSM-стейт, счётчик генераций, фото-сессии."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from zoneinfo import ZoneInfo

from app.utils.logging import logger

# Импортируем State только для сравнения в get_active_generations (не для создания стейтов)
from app.handlers.fsm import State  # noqa: F401


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    username       TEXT,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    topic       TEXT    NOT NULL,
    slides_n    INTEGER NOT NULL,
    photos_n    INTEGER NOT NULL,
    status      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generations_user_day
    ON generations(user_id, created_at);

CREATE TABLE IF NOT EXISTS fsm_state (
    user_id      INTEGER PRIMARY KEY,
    state        TEXT    NOT NULL,
    data_json    TEXT    NOT NULL DEFAULT '{}',
    updated_at   TEXT    NOT NULL
);
"""


class SQLiteStorage:
    """Обёртка над aiosqlite: схема, FSM, rate-limit, лог генераций."""

    def __init__(self, db_path: str, tz_name: str = "Europe/Moscow") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.tz = ZoneInfo(tz_name)
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        # isolation_level=None отключает авто-транзакции sqlite3,
        # что позволяет нам вручную управлять BEGIN IMMEDIATE.
        self._conn = await aiosqlite.connect(self.db_path, isolation_level=None)
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info("SQLite storage initialized at {}", self.db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def touch_user(self, user_id: int, username: str | None) -> None:
        now = self._now_iso()
        await self._conn.execute(
            "INSERT INTO users(user_id, username, first_seen_at, last_seen_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, "
            "username = excluded.username",
            (user_id, username, now, now),
        )
        await self._conn.commit()

    async def get_generations_today(self, user_id: int) -> int:
        """Считает генерации за текущие сутки по локальной TZ.
        Учитываются как успешные ('ok'), так и запущенные ('pending'),
        чтобы предотвратить race condition при одновременных запросах.
        """
        local_now = datetime.now(self.tz)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_utc = day_start.astimezone(timezone.utc).isoformat()
        cur = await self._conn.execute(
            "SELECT COUNT(*) FROM generations "
            "WHERE user_id = ? AND created_at >= ? AND status IN ('ok', 'pending')",
            (user_id, day_start_utc),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def get_active_generations(self, user_id: int) -> bool:
        """Проверяет, идёт ли реальная генерация прямо сейчас.

        Генерация «активна» только если ВЕРНО ВСЁ:
        1. Пользователь находится в стейте 'GENERATING'.
        2. При этом есть свежая (<10 мин) запись 'pending' в generations.

        Если пользователь в любом другом стейте (включая 'confirm_generation')
        — значит генерация не запущена, и старые pending-записи от прежних
        сессий не должны блокировать новый запуск.
        """
        import datetime as dt
        ten_minutes_ago = (
            datetime.now(self.tz) - dt.timedelta(minutes=10)
        ).astimezone(timezone.utc).isoformat()

        # Сначала проверяем pending-запись
        cur = await self._conn.execute(
            "SELECT 1 FROM generations "
            "WHERE user_id = ? AND status = 'pending' AND created_at >= ? "
            "LIMIT 1",
            (user_id, ten_minutes_ago),
        )
        has_pending = (await cur.fetchone()) is not None

        # Теперь проверяем FSM-стейт
        cur2 = await self._conn.execute(
            "SELECT state FROM fsm_state WHERE user_id = ?",
            (user_id,),
        )
        row = await cur2.fetchone()
        is_generating = row is not None and row[0] == State.GENERATING

        # Активна ТОЛЬКО если оба условия
        return has_pending and is_generating

    async def log_generation(
        self,
        user_id: int,
        topic: str,
        slides_n: int,
        photos_n: int,
        status: str,
    ) -> int:
        """Записывает попытку генерации и возвращает ID записи."""
        cur = await self._conn.execute(
            "INSERT INTO generations(user_id, topic, slides_n, photos_n, status, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (user_id, topic, slides_n, photos_n, status, self._now_iso()),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def update_generation_status(self, gen_id: int, status: str) -> None:
        """Обновляет статус конкретной генерации."""
        await self._conn.execute(
            "UPDATE generations SET status = ? WHERE id = ?",
            (status, gen_id),
        )
        await self._conn.commit()

    async def cleanup_pending_generations(self) -> int:
        """Переводит все 'pending' записи в 'error', чтобы они не блокировали лимит после перезапуска."""
        cur = await self._conn.execute(
            "UPDATE generations SET status = 'error' WHERE status = 'pending'"
        )
        await self._conn.commit()
        return cur.rowcount

    async def cleanup_stale_fsm_states(self) -> int:
        """Сбрасывает пользователей, застрявших в состоянии GENERATING
        (осталось от предыдущей сессии бота), в IDLE.
        Также очищает 'pending' записи, которые старше 10 минут.
        """
        # Сбрасываем застрявших в GENERATING
        cur = await self._conn.execute(
            "UPDATE fsm_state SET state = 'IDLE', data_json = '{}', updated_at = ? "
            "WHERE state = 'GENERATING'",
            (self._now_iso(),),
        )
        await self._conn.commit()
        count = cur.rowcount

        # Удаляем безнадёжно старые 'pending' (старше 10 мин)
        import datetime as dt
        ten_min_ago = (
            datetime.now(self.tz) - dt.timedelta(minutes=10)
        ).astimezone(timezone.utc).isoformat()
        cur2 = await self._conn.execute(
            "UPDATE generations SET status = 'error' "
            "WHERE status = 'pending' AND created_at < ?",
            (ten_min_ago,),
        )
        await self._conn.commit()
        total = cur2.rowcount + count
        if total:
            logger.info("Cleaned up {} stale FSM/generation states", total)
        return total

    async def force_clear_user_generation(self, user_id: int) -> None:
        """Принудительно снимает блокировку генерации для конкретного пользователя.
        Вызывается перед запуском новой генерации, если есть подозрение на зависшую.
        """
        await self._conn.execute(
            "UPDATE generations SET status = 'error' "
            "WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        )
        await self._conn.commit()
        logger.debug("Force-cleared pending generations for user {}", user_id)

    # ---------- FSM state ----------

    async def get_state(self, user_id: int) -> tuple[str | None, dict[str, Any]]:
        cur = await self._conn.execute(
            "SELECT state, data_json FROM fsm_state WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None, {}
        try:
            data = json.loads(row[1]) if row[1] else {}
        except json.JSONDecodeError:
            data = {}
        return row[0], data

    async def set_state(self, user_id: int, state: str, data: dict[str, Any] | None = None) -> None:
        payload = json.dumps(data or {}, ensure_ascii=False)
        await self._conn.execute(
            "INSERT INTO fsm_state(user_id, state, data_json, updated_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "state = excluded.state, data_json = excluded.data_json, updated_at = excluded.updated_at",
            (user_id, state, payload, self._now_iso()),
        )
        await self._conn.commit()

    async def clear_state(self, user_id: int) -> None:
        await self._conn.execute("DELETE FROM fsm_state WHERE user_id = ?", (user_id,))
        await self._conn.commit()

    async def add_photos(self, user_id: int, paths: list[str]) -> int:
        """Атомарно добавляет список фотографий в состояние пользователя.

        Использует BEGIN IMMEDIATE для предотвращения race condition (Read-Modify-Write).
        """
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await self._conn.execute(
                "SELECT data_json FROM fsm_state WHERE user_id = ?",
                (user_id,),
            )
            row = await cur.fetchone()

            data = {}
            if row:
                try:
                    data = json.loads(row[0]) if row[0] else {}
                except json.JSONDecodeError:
                    data = {}

            photos = data.get("photos", [])
            if not isinstance(photos, list):
                photos = []

            photos.extend(paths)
            data["photos"] = photos

            await self._conn.execute(
                "UPDATE fsm_state SET data_json = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(data, ensure_ascii=False), self._now_iso(), user_id),
            )
            await self._conn.commit()
            return len(photos)
        except Exception:
            await self._conn.rollback()
            raise



__all__ = ["SQLiteStorage"]