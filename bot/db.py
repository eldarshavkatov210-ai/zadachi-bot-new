"""PostgreSQL bilan ishlash (asyncpg)."""
from dataclasses import dataclass
from datetime import datetime

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    chat_id     BIGINT PRIMARY KEY,
    title       TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS checklists (
    chat_id     BIGINT NOT NULL REFERENCES groups(chat_id) ON UPDATE CASCADE ON DELETE CASCADE,
    message_id  BIGINT NOT NULL,
    title       TEXT,
    author      TEXT,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS checklist_tasks (
    chat_id     BIGINT NOT NULL,
    message_id  BIGINT NOT NULL,
    task_id     INTEGER NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    is_done     BOOLEAN NOT NULL DEFAULT FALSE,
    done_by     TEXT,
    done_at     TIMESTAMPTZ,
    PRIMARY KEY (chat_id, message_id, task_id),
    FOREIGN KEY (chat_id, message_id) REFERENCES checklists(chat_id, message_id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reminder_log (
    id          SERIAL PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    undone      INTEGER NOT NULL,
    ok          BOOLEAN NOT NULL,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_checklists_created ON checklists (chat_id, created_at);
"""


@dataclass
class TaskData:
    task_id: int
    text: str
    is_done: bool
    done_by: str | None = None
    done_at: datetime | None = None


class Database:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def create(cls, dsn: str) -> "Database":
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
        return cls(pool)

    async def close(self):
        await self.pool.close()

    async def init_schema(self):
        async with self.pool.acquire() as con:
            await con.execute(SCHEMA)

    # ---------------- guruhlar ----------------
    async def add_group(self, chat_id: int, title: str | None):
        await self.pool.execute(
            """INSERT INTO groups (chat_id, title, is_active) VALUES ($1, $2, TRUE)
               ON CONFLICT (chat_id) DO UPDATE
               SET is_active = TRUE, title = COALESCE(EXCLUDED.title, groups.title)""",
            chat_id, title,
        )

    async def ensure_group(self, chat_id: int, title: str | None):
        """.env dagi GROUP_IDS uchun: bor bo'lsa holatini o'zgartirmaydi."""
        await self.pool.execute(
            """INSERT INTO groups (chat_id, title) VALUES ($1, $2)
               ON CONFLICT (chat_id) DO UPDATE SET title = COALESCE(EXCLUDED.title, groups.title)""",
            chat_id, title,
        )

    async def set_group_active(self, chat_id: int, active: bool):
        await self.pool.execute("UPDATE groups SET is_active = $2 WHERE chat_id = $1", chat_id, active)

    async def update_title(self, chat_id: int, title: str):
        await self.pool.execute("UPDATE groups SET title = $2 WHERE chat_id = $1", chat_id, title)

    async def is_tracked(self, chat_id: int) -> bool:
        return bool(await self.pool.fetchval(
            "SELECT is_active FROM groups WHERE chat_id = $1", chat_id))

    async def active_groups(self) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT chat_id, title FROM groups WHERE is_active ORDER BY added_at, chat_id")

    async def migrate_group(self, old_id: int, new_id: int):
        """Oddiy guruh superguruhga aylanganda ID o'zgaradi — ma'lumotlarni ko'chiramiz."""
        async with self.pool.acquire() as con, con.transaction():
            exists = await con.fetchval("SELECT 1 FROM groups WHERE chat_id = $1", new_id)
            if exists:
                await con.execute(
                    """UPDATE groups SET is_active = TRUE,
                       title = COALESCE(title, (SELECT title FROM groups WHERE chat_id = $1))
                       WHERE chat_id = $2""", old_id, new_id)
                # eski checklistlarni yangi ID ga ko'chiramiz (vazifalar CASCADE bilan ko'chadi)
                await con.execute(
                    """UPDATE checklists SET chat_id = $2 WHERE chat_id = $1
                       AND message_id NOT IN (SELECT message_id FROM checklists WHERE chat_id = $2)""",
                    old_id, new_id)
                await con.execute("DELETE FROM groups WHERE chat_id = $1", old_id)
            else:
                await con.execute("UPDATE groups SET chat_id = $2 WHERE chat_id = $1", old_id, new_id)

    # ---------------- checklistlar ----------------
    async def save_checklist(self, chat_id: int, message_id: int, title: str,
                             created_at: datetime, author: str | None, tasks: list[TaskData]):
        async with self.pool.acquire() as con, con.transaction():
            await con.execute(
                """INSERT INTO checklists (chat_id, message_id, title, author, created_at)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (chat_id, message_id) DO UPDATE
                   SET title = EXCLUDED.title,
                       author = COALESCE(checklists.author, EXCLUDED.author),
                       updated_at = now()""",
                chat_id, message_id, title, author, created_at)
            for t in tasks:
                await con.execute(
                    """INSERT INTO checklist_tasks (chat_id, message_id, task_id, text, is_done, done_by, done_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (chat_id, message_id, task_id) DO UPDATE
                       SET text = EXCLUDED.text,
                           is_done = EXCLUDED.is_done,
                           done_by = CASE WHEN EXCLUDED.is_done
                                          THEN COALESCE(EXCLUDED.done_by, checklist_tasks.done_by) END,
                           done_at = CASE WHEN EXCLUDED.is_done
                                          THEN COALESCE(EXCLUDED.done_at, checklist_tasks.done_at, now()) END""",
                    chat_id, message_id, t.task_id, t.text, t.is_done, t.done_by, t.done_at)
            if tasks:
                await con.execute(
                    """DELETE FROM checklist_tasks WHERE chat_id = $1 AND message_id = $2
                       AND NOT (task_id = ANY($3::int[]))""",
                    chat_id, message_id, [t.task_id for t in tasks])

    async def mark_tasks(self, chat_id: int, message_id: int, task_ids: list[int],
                         done: bool, by: str | None, at: datetime | None):
        if not task_ids:
            return
        await self.pool.execute(
            """UPDATE checklist_tasks
               SET is_done = $4,
                   done_by = CASE WHEN $4 THEN $5 END,
                   done_at = CASE WHEN $4 THEN COALESCE($6, now()) END
               WHERE chat_id = $1 AND message_id = $2 AND task_id = ANY($3::int[])""",
            chat_id, message_id, task_ids, done, by, at)

    async def undone_tasks(self, chat_id: int, since: datetime) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            """SELECT c.message_id, c.title, c.created_at, t.task_id, t.text,
                      (SELECT count(*) FROM checklist_tasks x
                        WHERE x.chat_id = c.chat_id AND x.message_id = c.message_id) AS total
               FROM checklists c
               JOIN checklist_tasks t ON t.chat_id = c.chat_id AND t.message_id = c.message_id
               WHERE c.chat_id = $1 AND c.created_at >= $2 AND NOT t.is_done
               ORDER BY c.created_at, c.message_id, t.task_id""",
            chat_id, since)

    async def group_stats(self, chat_id: int, since: datetime) -> asyncpg.Record:
        return await self.pool.fetchrow(
            """SELECT count(DISTINCT c.message_id) AS checklists,
                      count(t.task_id) AS tasks,
                      count(t.task_id) FILTER (WHERE t.is_done) AS done
               FROM checklists c
               LEFT JOIN checklist_tasks t ON t.chat_id = c.chat_id AND t.message_id = c.message_id
               WHERE c.chat_id = $1 AND c.created_at >= $2""",
            chat_id, since)

    async def log_reminder(self, chat_id: int, undone: int, ok: bool, error: str | None = None):
        await self.pool.execute(
            "INSERT INTO reminder_log (chat_id, undone, ok, error) VALUES ($1, $2, $3, $4)",
            chat_id, undone, ok, error)
