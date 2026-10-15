"""Ishga tushirish: python -m bot"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .admin import router as admin_router
from .checker import apply_schedule
from .config import load_config
from .db import Database
from .handlers import router
from .settings import Settings

log = logging.getLogger("bot")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()

    db = await Database.create(config.database_url)
    await db.init_schema()
    settings = await Settings.load(db, config)

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

    scheduler = AsyncIOScheduler(timezone=config.tz)
    apply_schedule(scheduler, bot, db, config, settings)
    scheduler.start()

    dp = Dispatcher(db=db, config=config, settings=settings, scheduler=scheduler)
    dp.include_router(admin_router)  # admin panel birinchi: /start adminlar uchun panelni ochadi
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="admin", description="Admin panel"),
        BotCommand(command="hisobot", description="Hozirgi holat (guruhlarga yubormasdan)"),
        BotCommand(command="tekshir", description="Hoziroq tekshirib, eslatma yuborish"),
        BotCommand(command="guruhlar", description="Kuzatilayotgan guruhlar"),
        BotCommand(command="yordam", description="Yordam"),
    ], scope=BotCommandScopeAllPrivateChats())

    times = settings.times_text
    pause_note = " ⏸ Avtomatik tekshiruv pauzada." if settings.paused else ""
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, f"✅ Bot ishga tushdi. Tekshiruv vaqti: {times} "
                                             f"({config.tz.key}).{pause_note}\nPanel: /admin")
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
