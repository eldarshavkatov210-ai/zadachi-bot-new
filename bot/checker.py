"""Asosiy tekshiruv: bajarilmagan checklistlarni yig'adi, guruhlarga eslatma va adminga hisobot yuboradi."""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from .config import Config
from .db import Database
from .texts import GroupResult, Item, build_reminder, build_report, clean_title

log = logging.getLogger(__name__)


async def collect(db: Database, config: Config, now: datetime) -> list[GroupResult]:
    since = (now - timedelta(days=config.lookback_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    results = []
    for g in await db.active_groups():
        res = GroupResult(chat_id=g["chat_id"], title=g["title"] or str(g["chat_id"]))
        by_msg: dict[int, Item] = {}
        for row in await db.undone_tasks(g["chat_id"], since):
            it = by_msg.get(row["message_id"])
            if it is None:
                it = Item(
                    message_id=row["message_id"],
                    day=row["created_at"].astimezone(config.tz).date(),
                    title=clean_title(row["title"], g["title"]),
                    undone=[],
                    total=row["total"],
                )
                by_msg[row["message_id"]] = it
            it.undone.append(row["text"])
        res.items = list(by_msg.values())
        results.append(res)
    return results


async def send_chunks(bot: Bot, chat_id: int, chunks: list[str]):
    for chunk in chunks:
        await bot.send_message(chat_id, chunk)
        await asyncio.sleep(0.4)


async def run_check(bot: Bot, db: Database, config: Config,
                    send_to_groups: bool = True, report_to: list[int] | None = None):
    """send_to_groups=False — faqat hisobot (guruhlarga hech narsa ketmaydi)."""
    now = datetime.now(config.tz)
    results = await collect(db, config, now)

    if send_to_groups:
        for r in results:
            if not r.items:
                continue
            try:
                await send_chunks(bot, r.chat_id, build_reminder(r.chat_id, r.items, now.date()))
                r.sent = True
                await db.log_reminder(r.chat_id, r.undone_count, True)
            except Exception as e:  # guruhdan chiqarilgan, huquq yo'q va h.k.
                r.sent, r.error = False, str(e)[:200]
                log.exception("Eslatma yuborilmadi: %s", r.title)
                await db.log_reminder(r.chat_id, r.undone_count, False, r.error)

    report = build_report(results, now, "sent" if send_to_groups else "preview")
    for admin_id in (report_to if report_to is not None else config.admin_ids):
        try:
            await send_chunks(bot, admin_id, report)
        except Exception:
            log.exception("Hisobot yuborilmadi: %s", admin_id)
    return results
