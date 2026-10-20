import datetime as dt
from html import escape
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.callback_data import BookingActionCallback, ReminderActionCallback
from app.bot.keyboards.client_keyboards import cancel_confirm_keyboard, dates_keyboard
from app.bot.states.booking_states import RescheduleFSM
from app.database.repositories.booking_repository import BookingRepository
from app.database.models import Notification, NotificationStatus, NotificationType
from app.services.booking_service import BookingService
from app.utils.timezone import format_time_range, get_master_tz, to_local, weekday_name_ru
from sqlalchemy import select, update

router = Router(name="client_reminder")


@router.callback_query(ReminderActionCallback.filter(F.action == "still_valid"))
async def reminder_still_valid(
    callback: CallbackQuery, callback_data: ReminderActionCallback, session: AsyncSession
) -> None:
    booking = await BookingRepository(session).get_owned_by_telegram_id(callback_data.booking_id, callback.from_user.id)
    if booking is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    # Запись и так остаётся confirmed — просто отмечаем, что клиент подтвердил, и убираем кнопки.
    await session.execute(
        update(Notification)
        .where(
            Notification.booking_id == callback_data.booking_id,
            # Только 24-часовое напоминание: иначе «Всё в силе» погасило бы ещё не
            # отправленные напоминания за 2 часа.
            Notification.type == NotificationType.REMINDER_24H.value,
        )
        .values(status=NotificationStatus.SENT.value)
    )
    await session.commit()
    await callback.message.edit_text(callback.message.html_text + "\n\n✅ Спасибо, ждём вас!")
    await callback.answer()


@router.callback_query(ReminderActionCallback.filter(F.action == "cancel"))
async def reminder_cancel(callback: CallbackQuery, callback_data: ReminderActionCallback, session: AsyncSession) -> None:
    booking = await BookingRepository(session).get_owned_by_telegram_id(callback_data.booking_id, callback.from_user.id)
    if booking is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    tz = await get_master_tz(session)
    start_local = to_local(booking.start_datetime, tz)
    await callback.message.edit_text(
        "Вы действительно хотите отменить запись?\n\n"
        f"{start_local.strftime('%d.%m.%Y')} - {weekday_name_ru(start_local)}, {format_time_range(booking.start_datetime, booking.end_datetime, tz)}\n"
        f"{escape(booking.service_name_snapshot)}",
        reply_markup=cancel_confirm_keyboard(booking.id),
    )
    await callback.answer()


@router.callback_query(ReminderActionCallback.filter(F.action == "reschedule"))
async def reminder_reschedule(
    callback: CallbackQuery, callback_data: ReminderActionCallback, state: FSMContext, session: AsyncSession
) -> None:
    booking = await BookingRepository(session).get_owned_by_telegram_id(callback_data.booking_id, callback.from_user.id)
    if booking is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    tz = await get_master_tz(session)
    if not BookingService.is_modifiable(booking, dt.datetime.now(tz)):
        await callback.answer("Эту запись уже нельзя перенести.", show_alert=True)
        return
    booking_service = BookingService(session, tz)
    from app.bot.handlers.client.booking import DATES_PAGES, _find_first_dates_page

    found = await _find_first_dates_page(booking_service, booking.occupied_minutes, tz)
    if found is None:
        await callback.answer("Свободных дат пока нет.", show_alert=True)
        return
    page, dates = found

    await state.update_data(
        booking_id=booking.id, service_duration=booking.occupied_minutes, dates_page=page
    )
    await state.set_state(RescheduleFSM.select_date)
    await callback.message.edit_text(
        "📅 Выберите новую дату:", reply_markup=dates_keyboard(dates, page=page, total_pages=DATES_PAGES, with_back=False)
    )
    await callback.answer()
