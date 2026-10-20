import datetime as dt
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.callback_data import (
    BookingActionCallback,
    DateCallback,
    DatesPageCallback,
    NavCallback,
    TimeCallback,
)
from app.bot.keyboards.client_keyboards import (
    cancel_confirm_keyboard,
    dates_keyboard,
    my_booking_keyboard,
    times_keyboard,
)
from app.bot.states.booking_states import RescheduleFSM
from app.database.repositories.booking_repository import BookingRepository
from app.bot.middlewares.admin_filter import get_admin_contact_username
from app.database.repositories.service_repository import ServiceRepository
from app.database.repositories.user_repository import UserRepository
from app.services.booking_service import CANCEL_STREAK_ALERT, BookingService
from app.services.exceptions import (
    BookingNotCancellableError,
    BookingNotFoundError,
    ServiceInactiveError,
    ServiceNotFoundError,
    SlotNotAvailableError,
)
from app.services.notifier import MasterNotifier
from app.utils.money import format_price
from app.utils.timezone import format_time_range, get_master_tz, to_local, weekday_name_ru

router = Router(name="client_my_booking")


def _format_booking(b, tz: dt.tzinfo) -> str:
    start_local = to_local(b.start_datetime, tz)
    return (
        "📋 Ваша запись\n\n"
        f"{escape(b.service_name_snapshot)}\n\n"
        f"📅 {start_local.strftime('%d.%m.%Y')} - {weekday_name_ru(start_local)}\n"
        f"🕐 {format_time_range(b.start_datetime, b.end_datetime, tz)}\n\n"
        f"💰 {format_price(b.service_price_snapshot)} BYN\n\n"
        "Статус: ✅ Подтверждена"
    )


@router.message(F.text == "📋 Мои записи")
async def show_my_booking(message: Message, session: AsyncSession) -> None:
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer("У вас пока нет записей.")
        return

    tz = await get_master_tz(session)
    booking_repo = BookingRepository(session)
    upcoming = await booking_repo.get_user_upcoming(user.id, dt.datetime.now(tz))
    if not upcoming:
        await message.answer("⚠️ У вас пока нет активных записей.")
        return

    contact_username = await get_admin_contact_username(message.bot)

    for b in upcoming:
        await message.answer(_format_booking(b, tz), reply_markup=my_booking_keyboard(b.id, contact_username))


@router.callback_query(BookingActionCallback.filter(F.action == "cancel"))
async def ask_cancel_confirmation(
    callback: CallbackQuery, callback_data: BookingActionCallback, session: AsyncSession
) -> None:
    booking = await BookingRepository(session).get_owned_by_telegram_id(callback_data.booking_id, callback.from_user.id)
    if booking is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    tz = await get_master_tz(session)
    start_local = to_local(booking.start_datetime, tz)
    await callback.message.edit_text(
        "❓ Вы действительно хотите отменить запись?\n\n"
        f"📅 {start_local.strftime('%d.%m.%Y')} - {weekday_name_ru(start_local)}\n"
        f"🕐 {format_time_range(booking.start_datetime, booking.end_datetime, tz)}\n"
        f"{escape(booking.service_name_snapshot)}",
        reply_markup=cancel_confirm_keyboard(booking.id),
    )
    await callback.answer()


@router.callback_query(BookingActionCallback.filter(F.action == "cancel_back"))
async def cancel_back(callback: CallbackQuery, callback_data: BookingActionCallback, session: AsyncSession) -> None:
    booking = await BookingRepository(session).get_owned_by_telegram_id(callback_data.booking_id, callback.from_user.id)
    if booking is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    tz = await get_master_tz(session)
    contact_username = await get_admin_contact_username(callback.bot)
    await callback.message.edit_text(
        _format_booking(booking, tz), reply_markup=my_booking_keyboard(booking.id, contact_username)
    )
    await callback.answer()


@router.callback_query(BookingActionCallback.filter(F.action == "cancel_confirm"))
async def confirm_cancel(callback: CallbackQuery, callback_data: BookingActionCallback, session: AsyncSession) -> None:
    owned = await BookingRepository(session).get_owned_by_telegram_id(callback_data.booking_id, callback.from_user.id)
    if owned is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    tz = await get_master_tz(session)
    booking_service = BookingService(session, tz)
    user = await UserRepository(session).get_by_telegram_id(callback.from_user.id)

    try:
        booking = await booking_service.cancel_booking(owned.id, by_client=True)
        await session.commit()
    except BookingNotCancellableError:
        await session.rollback()
        await callback.answer("Эту запись уже нельзя отменить.", show_alert=True)
        return

    cancelled_local = to_local(booking.start_datetime, tz)
    price = booking.service_price_snapshot
    price_text = int(price) if price == int(price) else f"{price:.2f}"
    await callback.message.edit_text(
        "Запись отменена.\n\n"
        f"{escape(booking.service_name_snapshot)}\n"
        f"📅 {cancelled_local.strftime('%d.%m.%Y')} - {weekday_name_ru(cancelled_local)}\n"
        f"🕐 {format_time_range(booking.start_datetime, booking.end_datetime, tz)}\n"
        f"💰 {price_text} BYN"
    )
    await callback.answer()

    notifier = MasterNotifier(callback.bot)
    await notifier.notify_cancelled_booking(booking, user, tz)

    # Мастеру — предупреждение, он решает, что делать с доступом. Два случая:
    #  1) три и более самостоятельных записи подряд отменены клиентом (в тексте реальное число);
    #  2) у клиента было больше трёх записей (разрешение мастера «сверх лимита») и он отменил три любых из них.
    streak = await booking_service.cancellation_streak(user)
    over_limit_cancels = 0
    if booking.manual_client_name is None:  # записи, созданные мастером вручную, в счёт клиента не идут
        over_limit_cancels = await booking_service.note_over_limit_cancellation(user)
    if streak >= CANCEL_STREAK_ALERT or over_limit_cancels >= CANCEL_STREAK_ALERT:
        completed = await booking_service.bookings.count_completed_for_user(user.id)
        if streak >= CANCEL_STREAK_ALERT:
            await notifier.notify_cancellation_streak(user, completed, streak)
        else:
            await notifier.notify_cancellation_streak(user, completed, over_limit_cancels, over_limit=True)
        # Счёт начинается заново: если мастер не отреагирует, следующее предупреждение придёт после новых трёх отмен.
        user.cancel_streak_reset_at = dt.datetime.now(dt.timezone.utc)
        user.over_limit_cancels = None
    await session.commit()


# --- Перенос ---


@router.callback_query(BookingActionCallback.filter(F.action == "reschedule"))
async def start_reschedule(
    callback: CallbackQuery, callback_data: BookingActionCallback, state: FSMContext, session: AsyncSession
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


# Этот обработчик листает даты и при переносе из «Моих записей», и при переносе из напоминания:
# у обоих одно и то же состояние RescheduleFSM.select_date.
@router.callback_query(RescheduleFSM.select_date, DatesPageCallback.filter())
async def reschedule_change_dates_page(
    callback: CallbackQuery, callback_data: DatesPageCallback, state: FSMContext, session: AsyncSession
) -> None:
    from app.bot.handlers.client.booking import show_dates_page

    data = await state.get_data()
    tz = await get_master_tz(session)
    await show_dates_page(
        callback,
        state,
        BookingService(session, tz),
        data["service_duration"],
        tz,
        "📅 Выберите новую дату:",
        callback_data.page,
        with_back=False,
    )


@router.callback_query(RescheduleFSM.select_date, NavCallback.filter(F.target == "back"))
async def reschedule_back_to_booking(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """«Назад» на выборе даты — отказ от переноса: возвращаем карточку записи."""
    data = await state.get_data()
    await state.clear()
    booking = await BookingRepository(session).get_by_id(data["booking_id"])
    if booking is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    tz = await get_master_tz(session)
    contact_username = await get_admin_contact_username(callback.bot)
    await callback.message.edit_text(
        _format_booking(booking, tz), reply_markup=my_booking_keyboard(booking.id, contact_username)
    )
    await callback.answer()


@router.callback_query(RescheduleFSM.select_time, NavCallback.filter(F.target == "back"))
async def reschedule_back_to_dates(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """«Назад» на выборе времени — к датам, на ту же страницу."""
    from app.bot.handlers.client.booking import show_dates_page

    data = await state.get_data()
    tz = await get_master_tz(session)
    await state.set_state(RescheduleFSM.select_date)
    await show_dates_page(
        callback,
        state,
        BookingService(session, tz),
        data["service_duration"],
        tz,
        "📅 Выберите новую дату:",
        data.get("dates_page", 0),
        with_back=False,
    )


@router.callback_query(RescheduleFSM.select_date, DateCallback.filter())
async def reschedule_select_date(
    callback: CallbackQuery, callback_data: DateCallback, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    tz = await get_master_tz(session)
    booking_service = BookingService(session, tz)
    selected_date = dt.date.fromisoformat(callback_data.date)
    now = dt.datetime.now(tz)
    slots = await booking_service.get_available_slots(
        selected_date, data["service_duration"], now=now if selected_date == now.date() else None
    )
    if not slots:
        await callback.answer("На эту дату нет свободного времени.", show_alert=True)
        return

    await state.set_state(RescheduleFSM.select_time)
    await callback.message.edit_text(
        f"📅 {selected_date.strftime('%d.%m.%Y')}\n\n🕐 Свободное время:", reply_markup=times_keyboard(slots)
    )
    await callback.answer()


@router.callback_query(RescheduleFSM.select_time, TimeCallback.filter())
async def reschedule_select_time(
    callback: CallbackQuery, callback_data: TimeCallback, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    tz = await get_master_tz(session)
    booking_service = BookingService(session, tz)
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(callback.from_user.id)
    old_booking = await BookingRepository(session).get_by_id(data["booking_id"])
    new_start = dt.datetime.fromtimestamp(callback_data.ts, tz=tz)

    try:
        new_booking = await booking_service.reschedule_booking(data["booking_id"], new_start)
        await session.commit()
    except SlotNotAvailableError:
        await session.rollback()
        await callback.message.edit_text("❌ Это время только что заняли. Ваша старая запись осталась в силе.")
        await callback.answer()
        await state.clear()
        return
    except (ServiceInactiveError, ServiceNotFoundError):
        await session.rollback()
        await callback.message.edit_text(
            "❌ Эта услуга больше недоступна для записи. Ваша старая запись осталась в силе — "
            "чтобы выбрать другое время, свяжитесь с мастером."
        )
        await callback.answer()
        await state.clear()
        return
    except (BookingNotCancellableError, BookingNotFoundError):
        await session.rollback()
        await callback.message.edit_text("Эту запись уже нельзя перенести.")
        await callback.answer()
        await state.clear()
        return

    new_start_local = to_local(new_booking.start_datetime, tz)
    price = new_booking.service_price_snapshot
    price_text = int(price) if price == int(price) else f"{price:.2f}"
    contact_username = await get_admin_contact_username(callback.bot)
    await callback.message.edit_text(
        "✅ Запись перенесена!\n\n"
        f"{escape(new_booking.service_name_snapshot)}\n"
        f"📅 {new_start_local.strftime('%d.%m.%Y')} - {weekday_name_ru(new_start_local)}\n"
        f"🕐 {format_time_range(new_booking.start_datetime, new_booking.end_datetime, tz)}\n"
        f"💰 {price_text} BYN",
        reply_markup=my_booking_keyboard(new_booking.id, contact_username),
    )
    await callback.answer()
    await state.clear()

    notifier = MasterNotifier(callback.bot)
    await notifier.notify_rescheduled_booking(old_booking, new_booking, user, tz)