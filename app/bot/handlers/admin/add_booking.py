import datetime as dt
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.client.booking import DATES_PAGES, _find_first_dates_page, show_dates_page
from app.bot.keyboards.callback_data import (
    ConfirmCallback,
    DateCallback,
    DatesPageCallback,
    NavCallback,
    ServiceCallback,
    TimeCallback,
)
from app.bot.keyboards.client_keyboards import (
    confirm_keyboard,
    dates_keyboard,
    my_booking_keyboard,
    services_keyboard,
    times_keyboard,
)
from app.bot.middlewares.admin_filter import IsAdminFilter, get_admin_contact_username
from app.bot.states.booking_states import AdminAddBookingFSM
from app.database.repositories.service_repository import ServiceRepository
from app.database.repositories.user_repository import UserRepository
from app.services.booking_service import BookingService
from app.services.exceptions import SlotNotAvailableError
from app.utils.contact import CONTACT_INVALID_TEXT, CONTACT_PROMPT, normalize_contact
from app.utils.invite import build_invite_link, build_share_url, new_invite_token
from app.utils.money import format_price
from app.utils.timezone import format_time_range, get_master_tz, to_local, weekday_name_ru

router = Router(name="admin_add_booking")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


@router.message(F.text == "➕ Добавить запись")
async def start_admin_booking(message: Message, state: FSMContext, session: AsyncSession) -> None:
    services = await ServiceRepository(session).get_active()
    if not services:
        await message.answer("Нет активных услуг.")
        return
    await state.set_state(AdminAddBookingFSM.select_service)
    await message.answer("Выберите услугу", reply_markup=services_keyboard(services))


@router.callback_query(AdminAddBookingFSM.select_service, ServiceCallback.filter())
async def admin_select_service(
    callback: CallbackQuery, callback_data: ServiceCallback, state: FSMContext, session: AsyncSession
) -> None:
    service = await ServiceRepository(session).get_by_id(callback_data.service_id)
    tz = await get_master_tz(session)
    booking_service = BookingService(session, tz)
    found = await _find_first_dates_page(booking_service, service.occupied_minutes, tz)
    if found is None:
        await callback.message.edit_text("Свободных дат нет.")
        await callback.answer()
        return
    page, dates = found
    await state.update_data(service_id=service.id, dates_page=page)
    await state.set_state(AdminAddBookingFSM.select_date)
    await callback.message.edit_text(
        "📅 Выберите дату:", reply_markup=dates_keyboard(dates, page=page, total_pages=DATES_PAGES)
    )
    await callback.answer()


@router.callback_query(AdminAddBookingFSM.select_date, DatesPageCallback.filter())
async def admin_change_dates_page(
    callback: CallbackQuery, callback_data: DatesPageCallback, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    service = await ServiceRepository(session).get_by_id(data["service_id"])
    tz = await get_master_tz(session)
    await show_dates_page(
        callback,
        state,
        BookingService(session, tz),
        service.occupied_minutes,
        tz,
        "📅 Выберите дату:",
        callback_data.page,
    )


@router.callback_query(AdminAddBookingFSM.select_date, NavCallback.filter(F.target == "back"))
async def admin_back_to_services(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    services = await ServiceRepository(session).get_active()
    await state.set_state(AdminAddBookingFSM.select_service)
    await callback.message.edit_text("Выберите услугу", reply_markup=services_keyboard(services))
    await callback.answer()


@router.callback_query(AdminAddBookingFSM.select_time, NavCallback.filter(F.target == "back"))
async def admin_back_to_dates(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    service = await ServiceRepository(session).get_by_id(data["service_id"])
    tz = await get_master_tz(session)
    await state.set_state(AdminAddBookingFSM.select_date)
    await show_dates_page(
        callback,
        state,
        BookingService(session, tz),
        service.occupied_minutes,
        tz,
        "📅 Выберите дату:",
        data.get("dates_page", 0),
    )


@router.callback_query(AdminAddBookingFSM.select_date, DateCallback.filter())
async def admin_select_date(
    callback: CallbackQuery, callback_data: DateCallback, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    service = await ServiceRepository(session).get_by_id(data["service_id"])
    tz = await get_master_tz(session)
    booking_service = BookingService(session, tz)
    selected_date = dt.date.fromisoformat(callback_data.date)
    now = dt.datetime.now(tz)
    slots = await booking_service.get_available_slots(
        selected_date, service.occupied_minutes, now=now if selected_date == now.date() else None
    )
    if not slots:
        await callback.answer("На эту дату нет свободного времени.", show_alert=True)
        return
    await state.set_state(AdminAddBookingFSM.select_time)
    await callback.message.edit_text("🕐 Выберите время:", reply_markup=times_keyboard(slots, with_main_menu=False))
    await callback.answer()


@router.callback_query(AdminAddBookingFSM.select_time, TimeCallback.filter())
async def admin_select_time(
    callback: CallbackQuery, callback_data: TimeCallback, state: FSMContext, session: AsyncSession
) -> None:
    tz = await get_master_tz(session)
    start_dt = dt.datetime.fromtimestamp(callback_data.ts, tz=tz)
    await state.update_data(selected_time=start_dt.isoformat())
    await state.set_state(AdminAddBookingFSM.enter_client_name)
    await callback.message.edit_text("Введите имя клиента:")
    await callback.answer()


@router.message(AdminAddBookingFSM.enter_client_name)
async def admin_enter_name(message: Message, state: FSMContext) -> None:
    await state.update_data(client_name=message.text)
    await state.set_state(AdminAddBookingFSM.enter_client_contact)
    await message.answer(CONTACT_PROMPT)


@router.message(AdminAddBookingFSM.enter_client_contact)
async def admin_enter_contact(message: Message, state: FSMContext, session: AsyncSession) -> None:
    contact = normalize_contact(message.text)
    if contact is None:
        # Остаёмся в этом же состоянии: мастер просто вводит контакт заново.
        await message.answer(CONTACT_INVALID_TEXT)
        return
    await state.update_data(client_contact=contact)
    data = await state.get_data()
    service = await ServiceRepository(session).get_by_id(data["service_id"])
    start_dt = dt.datetime.fromisoformat(data["selected_time"])
    await state.set_state(AdminAddBookingFSM.confirm)
    text = (
        "📋 Проверьте запись:\n\n"
        f"👤 Клиент: {escape(data['client_name'])} ({escape(data['client_contact'])})\n"
        f"Услуга: {escape(service.name)}\n"
        f"📅 {start_dt.strftime('%d.%m.%Y')}\n"
        f"🕐 {format_time_range(start_dt, start_dt + dt.timedelta(minutes=service.duration_minutes))}\n"
        f"💰 {format_price(service.price)} BYN"
    )
    await message.answer(text, reply_markup=confirm_keyboard())


@router.callback_query(AdminAddBookingFSM.confirm, ConfirmCallback.filter(F.action == "yes"))
async def admin_confirm_booking(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    tz = await get_master_tz(session)
    booking_service = BookingService(session, tz)

    user_repo = UserRepository(session)

    # Если контакт клиента — это @username уже существующего в боте пользователя,
    # привязываем запись к нему самому: тогда она будет видна ему в "Мои записи"
    # и по ней сработают автонапоминания. Если такого пользователя нет (телефон
    # или незнакомый username), запись формально числится за мастером — как и раньше,
    # реальные данные клиента лежат в manual_client_name/manual_client_contact.
    contact = data["client_contact"].strip()
    owner_user = None
    if contact.startswith("@"):
        owner_user = await user_repo.get_by_username(contact.lstrip("@"))

    if owner_user is None:
        owner_user = await user_repo.get_or_create(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
        )

    # Запись числится за мастером (клиента в боте нет) — даём ей ссылку-приглашение: когда клиент
    # откроет её, запись привяжется к нему и он начнёт получать напоминания.
    needs_invite = owner_user.telegram_id == callback.from_user.id

    try:
        booking = await booking_service.create_booking(
            owner_user.id,
            data["service_id"],
            dt.datetime.fromisoformat(data["selected_time"]),
            manual_client_name=data["client_name"],
            manual_client_contact=data["client_contact"],
        )
        if needs_invite:
            booking.invite_token = new_invite_token()
        await session.commit()
    except SlotNotAvailableError:
        await session.rollback()
        await callback.message.edit_text("❌ Это время уже занято.")
        await callback.answer()
        await state.clear()
        return

    notified = await _notify_client_if_possible(callback, owner_user, booking, tz)
    if not notified and booking.invite_token is None:
        # Клиент есть в базе бота, но написать ему не вышло (например, он заблокировал бота): запись уже
        # привязана к нему, а мастеру нужна ссылка, которую можно отправить другим способом.
        booking.invite_token = new_invite_token()
        await session.commit()
    invite_keyboard = await _invite_keyboard(callback, booking, tz) if not notified else None
    await callback.message.edit_text(
        _admin_booking_card(
            booking,
            data["client_name"],
            data["client_contact"],
            tz,
            notified,
            invite=invite_keyboard is not None,
            client_in_bot=not needs_invite,
        ),
        reply_markup=invite_keyboard,
    )
    await callback.answer()
    await state.clear()


async def _invite_keyboard(callback: CallbackQuery, booking, tz: dt.tzinfo) -> InlineKeyboardMarkup | None:
    """Кнопка «Отправить клиенту»: открывает окно пересылки Telegram с приглашением в бота.
    None, если ссылку собрать не удалось (нет токена или не получилось узнать username бота)."""
    if not booking.invite_token:
        return None
    try:
        bot_username = (await callback.bot.me()).username  # bot.me() кэшируется — лишних запросов к Telegram нет
    except Exception:
        return None
    if not bot_username:
        return None

    start_local = to_local(booking.start_datetime, tz)
    text = (
        f"Вы записаны: {booking.service_name_snapshot} — {start_local.strftime('%d.%m.%Y')} "
        f"в {format_time_range(booking.start_datetime, booking.end_datetime, tz)}. Откройте бота по ссылке, и запись с напоминаниями появится у вас."
    )
    share_url = build_share_url(build_invite_link(bot_username, booking.invite_token), text)
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📨 Отправить клиенту", url=share_url)]])


def _admin_booking_card(
    booking,
    client_name: str,
    client_contact: str,
    tz: dt.tzinfo,
    notified: bool,
    invite: bool = False,
    client_in_bot: bool = False,
) -> str:
    """Сообщение мастеру после создания записи: данные записи + статус уведомления клиента.

    client_in_bot — клиент найден среди пользователей бота, но написать ему не удалось."""
    if notified:
        header = "✅ Запись создана, клиент уведомлен"
    elif invite and client_in_bot:
        header = (
            "✅ Запись создана\n\n"
            "⚠️ Не удалось написать клиенту в Telegram (возможно, он заблокировал бота). "
            "Отправьте ему приглашение кнопкой ниже: когда он откроет ссылку, "
            "запись с напоминаниями вернётся к нему"
        )
    elif invite:
        header = (
            "✅ Запись создана\n\n"
            "📨 Клиента ещё нет в боте. Отправьте ему приглашение кнопкой ниже: когда он откроет ссылку, "
            "запись привяжется к нему и он начнёт получать напоминания"
        )
    else:
        header = "✅ Запись создана\n\n⚠️ Клиента не удалось уведомить в Telegram — сообщите ему сами"

    contact = client_contact.strip()
    contact_label = "Telegram" if contact.startswith("@") else "Телефон"
    start_local = to_local(booking.start_datetime, tz)
    return (
        f"{header}\n\n"
        f"👤 Клиент: {escape(client_name)}\n\n"
        f"📱 {contact_label}: {escape(contact)}\n\n"
        f"{escape(booking.service_name_snapshot)}\n\n"
        f"📅 {start_local.strftime('%d.%m.%Y')} - {weekday_name_ru(start_local)}\n\n"
        f"🕐 {format_time_range(booking.start_datetime, booking.end_datetime, tz)}\n\n"
        f"💰 {format_price(booking.service_price_snapshot)} BYN"
    )


async def _notify_client_if_possible(
    callback: CallbackQuery,
    client_user,
    booking,
    tz: dt.tzinfo,
) -> bool:
    """Уведомляет клиента о записи, только если он опознан как существующий
    пользователь бота (client_user.telegram_id реально принадлежит клиенту,
    а не мастеру — иначе у бота нет с ним диалога и написать ему невозможно).
    Возвращает True, если сообщение клиенту отправлено."""
    if client_user.telegram_id == callback.from_user.id:
        return False

    start_local = to_local(booking.start_datetime, tz)
    price = booking.service_price_snapshot
    price_text = int(price) if price == int(price) else f"{price:.2f}"
    text = (
        "📝 Мастер создал для вас запись\n\n"
        f"{escape(booking.service_name_snapshot)}\n"
        f"📅 {start_local.strftime('%d.%m.%Y')} - {weekday_name_ru(start_local)}\n"
        f"🕐 {format_time_range(booking.start_datetime, booking.end_datetime, tz)}\n"
        f"💰 {price_text} BYN\n\n"
        "🔔 Вы получите уведомление за 24 и за 2 часа 🙂"
    )
    contact_username = await get_admin_contact_username(callback.bot)
    try:
        await callback.bot.send_message(
            client_user.telegram_id, text, reply_markup=my_booking_keyboard(booking.id, contact_username)
        )
    except Exception:
        return False
    return True


@router.callback_query(AdminAddBookingFSM.confirm, ConfirmCallback.filter(F.action == "edit"))
async def admin_edit_booking(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    services = await ServiceRepository(session).get_active()
    await state.set_state(AdminAddBookingFSM.select_service)
    await callback.message.edit_text("Выберите услугу", reply_markup=services_keyboard(services))
    await callback.answer()