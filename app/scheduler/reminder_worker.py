import datetime as dt
import logging
from html import escape

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.keyboards.reminder_keyboards import reminder_keyboard
from app.config.settings import settings
from app.database.models import Booking, MasterProfile, NotificationType, User
from app.services.admin_reminder_service import AdminReminderService
from app.services.booking_service import complete_past_bookings
from app.services.reminder_service import ReminderService
from app.utils.reminder_texts import (
    client_reminder_2h_text,
    client_reminder_24h_text,
    master_reminder_2h_text,
)
from app.utils.timezone import format_time_range, get_master_tz, to_local

logger = logging.getLogger(__name__)


def is_unclaimed_manual_booking(booking: Booking, owner: User) -> bool:
    """Запись, которую мастер создал вручную для человека, которого ещё нет в боте.

    Пока клиент не открыл ссылку-приглашение, запись числится за самим мастером, а настоящие данные клиента лежат
    в manual_client_*. Напоминания «клиенту» по такой записи нельзя слать владельцу: они пришли бы мастеру.
    """
    return booking.manual_client_name is not None and owner.telegram_id == settings.admin_telegram_id


def make_send_reminder_func(bot: Bot, session_factory: async_sessionmaker):
    async def send_reminder(booking: Booking) -> None:
        async with session_factory() as session:
            user = await session.get(User, booking.user_id)
            profile_result = await session.execute(MasterProfile.__table__.select().limit(1))
            profile_row = profile_result.first()
            address = profile_row.address if profile_row else None
            tz = await get_master_tz(session)

        if is_unclaimed_manual_booking(booking, user):
            logger.info("Напоминание за сутки пропущено: клиент по записи %s ещё не открыл приглашение", booking.id)
            return

        text = client_reminder_24h_text(
            booking.service_name_snapshot,
            format_time_range(booking.start_datetime, booking.end_datetime, tz),
            booking.service_price_snapshot,
            address,
        )

        await bot.send_message(user.telegram_id, text, reply_markup=reminder_keyboard(booking.id))

    return send_reminder


def make_send_client_reminder_2h_func(bot: Bot, session_factory: async_sessionmaker):
    """Напоминание клиенту за 2 часа: «Через 2 часа у вас запись к Анне: Маникюр, 15:00»."""

    async def send_client_reminder_2h(booking: Booking) -> None:
        if booking.start_datetime <= dt.datetime.now(dt.timezone.utc):
            return  # воркер простоял и опоздал — «через 2 часа» уже неправда
        async with session_factory() as session:
            user = await session.get(User, booking.user_id)
            profile_result = await session.execute(MasterProfile.__table__.select().limit(1))
            profile_row = profile_result.first()
            master_name = (profile_row.name if profile_row else "") or ""  # имя могли удалить
            tz = await get_master_tz(session)

        if is_unclaimed_manual_booking(booking, user):
            logger.info("Напоминание за 2 часа пропущено: клиент по записи %s ещё не открыл приглашение", booking.id)
            return

        start_local = to_local(booking.start_datetime, tz)
        text = client_reminder_2h_text(
            master_name, booking.service_name_snapshot, format_time_range(booking.start_datetime, booking.end_datetime, tz)
        )
        await bot.send_message(user.telegram_id, text)

    return send_client_reminder_2h


def make_send_master_reminder_2h_func(bot: Bot, session_factory: async_sessionmaker):
    """Напоминание мастеру за 2 часа: имя клиента, услуга, время, @username, длительность, цена."""

    async def send_master_reminder_2h(booking: Booking) -> None:
        if not settings.admin_telegram_id:
            return
        if booking.start_datetime <= dt.datetime.now(dt.timezone.utc):
            return
        async with session_factory() as session:
            user = await session.get(User, booking.user_id)
            tz = await get_master_tz(session)

        start_local = to_local(booking.start_datetime, tz)
        unclaimed = is_unclaimed_manual_booking(booking, user)
        text = master_reminder_2h_text(
            client_first_name=user.first_name,
            client_username=user.username,
            service_name=booking.service_name_snapshot,
            time_str=format_time_range(booking.start_datetime, booking.end_datetime, tz),
            duration_minutes=booking.service_duration_snapshot,
            price=booking.service_price_snapshot,
            manual_client_name=booking.manual_client_name if unclaimed else None,
            manual_client_contact=booking.manual_client_contact if unclaimed else None,
        )
        await bot.send_message(settings.admin_telegram_id, text)

    return send_master_reminder_2h


def make_send_admin_reminder_func(bot: Bot):
    """Личное напоминание мастера самому себе: текст, который он сам задал."""

    async def send_admin_reminder(text: str) -> None:
        await bot.send_message(settings.admin_telegram_id, f"🔔 <b>Напоминание</b>\n\n{escape(text)}")

    return send_admin_reminder


def start_reminder_scheduler(bot: Bot, session_factory: async_sessionmaker) -> AsyncIOScheduler:
    """Периодически сканирует notifications и отправляет due-напоминания.
    Переживает рестарт приложения — источник истины полностью в БД (таблица notifications),
    APScheduler здесь используется только как "тикающие часы", а не как хранилище состояния.
    """
    scheduler = AsyncIOScheduler()
    send_func = make_send_reminder_func(bot, session_factory)
    send_funcs_by_type = {
        NotificationType.REMINDER_2H_CLIENT.value: make_send_client_reminder_2h_func(bot, session_factory),
        NotificationType.REMINDER_2H_MASTER.value: make_send_master_reminder_2h_func(bot, session_factory),
    }

    async def job():
        async with session_factory() as session:
            reminder_service = ReminderService(
                session, send_message_func=send_func, send_funcs_by_type=send_funcs_by_type
            )
            try:
                sent = await reminder_service.process_due_notifications(now=dt.datetime.now(dt.timezone.utc))
                if sent:
                    logger.info("Sent %s reminders", sent)
            except Exception:
                logger.exception("Reminder job failed")

    send_admin_reminder = make_send_admin_reminder_func(bot)

    async def admin_reminder_job():
        if not settings.admin_telegram_id:
            return
        async with session_factory() as session:
            try:
                tz = await get_master_tz(session)
                sent = await AdminReminderService(session, tz).process_due(send_admin_reminder)
                if sent:
                    logger.info("Sent %s admin reminders", sent)
            except Exception:
                logger.exception("Admin reminder job failed")

    async def completion_job():
        async with session_factory() as session:
            try:
                tz = await get_master_tz(session)
                completed = await complete_past_bookings(session, dt.datetime.now(tz))
                if completed:
                    logger.info("Marked %s bookings as completed", completed)
            except Exception:
                logger.exception("Booking completion job failed")

    scheduler.add_job(job, "interval", seconds=settings.reminder_poll_interval_seconds, id="reminder_worker")
    scheduler.add_job(
        admin_reminder_job,
        "interval",
        seconds=settings.reminder_poll_interval_seconds,
        id="admin_reminder_worker",
    )
    scheduler.add_job(
        completion_job,
        "interval",
        seconds=settings.reminder_poll_interval_seconds,
        id="booking_completion_worker",
    )
    scheduler.start()
    return scheduler
