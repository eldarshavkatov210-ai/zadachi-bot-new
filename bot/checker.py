"""Asosiy tekshiruv: bajarilmagan checklistlarni yig'adi, guruhlarga eslatma va adminga hisobot yuboradi."""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config
from .db import Database
from .settings import Settings
from .texts import GroupResult, Item, build_reminder, build_report, clean_title

log = logging.getLogger(__name__)

JOB_PREFIX = "check-"


async def collect(db: Database, settings: Settings, config: Config, now: datetime,
                  only_chat: int | None = None) -> list[GroupResult]:
    since = (now - timedelta(days=settings.lookback_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    results = []
    for g in await db.active_groups():
        if only_chat is not None and g["chat_id"] != only_chat:
            continue
        res = GroupResult(chat_id=g["chat_id"], title=g["title"] or str(g["chat_id"]),
                          reminders_enabled=g["reminders_enabled"])
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


async def run_check(bot: Bot, db: Database, config: Config, settings: Settings,
                    send_to_groups: bool = True, report_to: list[int] | None = None,
                    only_chat: int | None = None, force: bool = False):
    """send_to_groups=False — faqat hisobot (guruhlarga hech narsa ketmaydi).
    force=True — guruh uchun eslatmalar o'chirilgan bo'lsa ham yuboradi (paneldan qo'lda)."""
    now = datetime.now(config.tz)
    results = await collect(db, settings, config, now, only_chat)

    if send_to_groups:
        for r in results:
            if not r.items or not (r.reminders_enabled or force):
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
    for admin_id in (report_to if report_to is not None else settings.admin_ids):
        try:
            await send_chunks(bot, admin_id, report)
        except Exception:
            log.exception("Hisobot yuborilmadi: %s", admin_id)
    return results


async def scheduled_check(bot: Bot, db: Database, config: Config, settings: Settings):
    """Jadval bo'yicha tekshiruv — sozlamalar har safar yangidan o'qiladi."""
    if settings.paused:
        log.info("Bot pauzada — jadvaldagi tekshiruv o'tkazib yuborildi")
        return
    await run_check(bot, db, config, settings, send_to_groups=settings.send_to_groups)


def apply_schedule(scheduler: AsyncIOScheduler, bot: Bot, db: Database, config: Config, settings: Settings):
    """Tekshiruv vaqtlarini sozlamalarga moslab qayta yozadi."""
    for job in scheduler.get_jobs():
        if job.id.startswith(JOB_PREFIX):
            job.remove()
    for h, m in settings.remind_times:
        scheduler.add_job(
            scheduled_check, CronTrigger(hour=h, minute=m, timezone=config.tz),
            id=f"{JOB_PREFIX}{h:02d}{m:02d}",
            kwargs={"bot": bot, "db": db, "config": config, "settings": settings},
            misfire_grace_time=3600, coalesce=True, max_instances=1,
        )


def next_run(scheduler: AsyncIOScheduler) -> datetime | None:
    times = [j.next_run_time for j in scheduler.get_jobs()
             if j.id.startswith(JOB_PREFIX) and j.next_run_time]
    return min(times) if times else None
