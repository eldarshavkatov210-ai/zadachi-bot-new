"""Ishga tushirish: python -m bot"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .checker import run_check
from .config import load_config
from .db import Database
from .handlers import router

log = logging.getLogger("bot")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()

    db = await Database.create(config.database_url)
    await db.init_schema()

    bot = Bot(config.bot_token, default=DefaultBotProperties(
        parse_mode=ParseMode.HTML, link_preview_is_disabled=True))

    # .env dagi GROUP_IDS ni bazaga yozib qo'yamiz
    for gid in config.group_ids:
        title = None
        try:
            title = (await bot.get_chat(gid)).title
        except Exception as e:
            log.warning("Guruh %s ma'lumotini olib bo'lmadi: %s", gid, e)
        await db.ensure_group(gid, title)

    dp = Dispatcher(db=db, config=config)
    dp.include_router(router)

    scheduler = AsyncIOScheduler(timezone=config.tz)
    for h, m in config.remind_times:
        scheduler.add_job(
            run_check, CronTrigger(hour=h, minute=m, timezone=config.tz),
            kwargs={"bot": bot, "db": db, "config": config, "send_to_groups": config.send_to_groups},
            misfire_grace_time=3600, coalesce=True, max_instances=1,
        )
    scheduler.start()

    await bot.set_my_commands([
        BotCommand(command="hisobot", description="Hozirgi holat (guruhlarga yubormasdan)"),
        BotCommand(command="tekshir", description="Hoziroq tekshirib, eslatma yuborish"),
        BotCommand(command="guruhlar", description="Kuzatilayotgan guruhlar"),
        BotCommand(command="yordam", description="Yordam"),
    ], scope=BotCommandScopeAllPrivateChats())

    times = ", ".join(f"{h:02d}:{m:02d}" for h, m in config.remind_times)
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, f"✅ Bot ishga tushdi. Tekshiruv vaqti: {times} ({config.tz.key}).")
        except Exception:
            log.warning("Admin %s ga xabar yuborib bo'lmadi (botga /start bosilmagan bo'lishi mumkin)", admin_id)

    await bot.delete_webhook(drop_pending_updates=False)
    log.info("Bot ishga tushdi, tekshiruv vaqti: %s", times)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
