import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from app.bot.handlers.admin import about as admin_about
from app.bot.handlers.admin import access_requests as admin_access_requests
from app.bot.handlers.admin import add_booking as admin_add_booking
from app.bot.handlers.admin import block_time as admin_block_time
from app.bot.handlers.admin import bookings as admin_bookings
from app.bot.handlers.admin import content as admin_content
from app.bot.handlers.admin import portfolio as admin_portfolio
from app.bot.handlers.admin import reminders as admin_reminders
from app.bot.handlers.admin import reviews as admin_reviews
from app.bot.handlers.admin import schedule as admin_schedule
from app.bot.handlers.admin import client_access as admin_client_access
from app.bot.handlers.admin import limit_requests as admin_limit_requests
from app.bot.handlers.admin import services as admin_services
from app.bot.handlers.admin import stats as admin_stats
from app.bot.handlers.client import booking as client_booking
from app.bot.handlers.client import bot_blocked as client_bot_blocked
from app.bot.handlers.client import check_time as client_check_time
from app.bot.handlers.client import content as client_content
from app.bot.handlers.client import contact_master as client_contact_master
from app.bot.handlers.client import master_request as client_master_request
from app.bot.handlers.client import my_booking as client_my_booking
from app.bot.handlers.client import reminder_actions as client_reminder_actions
from app.bot.handlers.client import start as client_start
from app.bot.middlewares.db_session import DatabaseSessionMiddleware
from app.bot.middlewares.error_handling import ErrorHandlingMiddleware
from app.bot.middlewares.reset_input import ResetInputOnMenuPressMiddleware
from app.config.settings import settings
from app.database.session import async_session_factory
from app.scheduler.reminder_worker import start_reminder_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(ErrorHandlingMiddleware())
    dp.update.middleware(DatabaseSessionMiddleware(async_session_factory))
    dp.message.outer_middleware(ResetInputOnMenuPressMiddleware())

    # Порядок важен: admin-роутеры первыми (у них есть свой IsAdminFilter, так что обычным
    # пользователям они прозрачны), затем клиентские.
    # Обращение ограниченного клиента к мастеру должно стоять раньше admin_content: тот тоже ловит «📞 Связь с мастером».
    dp.include_router(client_contact_master.router)
    dp.include_router(admin_bookings.router)
    dp.include_router(admin_add_booking.router)
    dp.include_router(admin_schedule.router)
    dp.include_router(admin_block_time.router)
    dp.include_router(admin_services.router)
    dp.include_router(admin_client_access.router)
    dp.include_router(admin_limit_requests.router)
    dp.include_router(admin_access_requests.router)
    dp.include_router(admin_stats.router)
    dp.include_router(admin_content.router)
    dp.include_router(admin_portfolio.router)
    dp.include_router(admin_reviews.router)
    dp.include_router(admin_about.router)
    dp.include_router(admin_reminders.router)

    dp.include_router(client_bot_blocked.router)
    dp.include_router(client_master_request.router)
    dp.include_router(client_start.router)
    dp.include_router(client_booking.router)
    dp.include_router(client_my_booking.router)
    dp.include_router(client_reminder_actions.router)
    dp.include_router(client_check_time.router)
    dp.include_router(client_content.router)

    scheduler = start_reminder_scheduler(bot, async_session_factory)

    try:
        logger.info("Starting bot polling")
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
