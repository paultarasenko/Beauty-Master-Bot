import datetime as dt

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.callback_data import DateCallback, DatesPageCallback, NavCallback, ScheduleBookCallback
from app.bot.keyboards.client_keyboards import (
    booking_day_choice_keyboard,
    dates_keyboard,
    info_menu_keyboard,
    start_booking_inline_keyboard,
)
from app.bot.middlewares.admin_filter import is_admin_id
from app.bot.states.booking_states import CheckTimeFSM
from app.database.models import WorkingHours
from app.database.repositories.schedule_repository import ScheduleRepository
from app.services.booking_service import BookingService
from app.utils.schedule_image import render_day_schedule_image
from app.utils.timezone import get_master_tz

router = Router(name="client_check_time")

# Та же пагинация, что и при выборе даты записи (см. booking.py), но без привязки к услуге —
# показываем все рабочие дни мастера, а не только те, где остались свободные слоты нужной длительности.
DATES_PAGE_DAYS = 30
DATES_PAGES = 3


async def _working_hours_by_weekday(schedule_repo: ScheduleRepository) -> dict[int, WorkingHours]:
    result: dict[int, WorkingHours] = {}
    for weekday in range(7):
        working_hours = await schedule_repo.get_working_hours(weekday)
        if working_hours and working_hours.is_working:
            result[weekday] = working_hours
    return result


async def _today_has_free_time(booking_service: BookingService, today: dt.date, now: dt.datetime) -> bool:
    """Помещается ли на сегодня хотя бы самая короткая активная услуга (после текущего момента).

    Учитываются записи, перерывы и блокировки. Если активных услуг нет, ориентируемся просто
    на наличие свободного времени позже текущего момента.
    """
    active_services = await booking_service.services.get_active()
    if active_services:
        shortest = min(service.occupied_minutes for service in active_services)
        return await booking_service.has_any_slot(today, shortest, now)
    _, free_intervals, _ = await booking_service.get_day_schedule(today)
    return any(interval.end > now for interval in free_intervals)


async def _find_working_dates(
    schedule_repo: ScheduleRepository, booking_service: BookingService, tz: dt.tzinfo, page: int
) -> list[dt.date]:
    working_hours_by_weekday = await _working_hours_by_weekday(schedule_repo)
    now = dt.datetime.now(tz)
    dates = []
    for offset in range(page * DATES_PAGE_DAYS, (page + 1) * DATES_PAGE_DAYS):
        candidate = (now + dt.timedelta(days=offset)).date()
        working_hours = working_hours_by_weekday.get(candidate.weekday())
        if working_hours is None:
            continue
        if candidate == now.date():
            # Сегодня рабочее время уже закончилось — кнопки этой даты в меню быть не должно.
            if working_hours.end_time is not None and now.time() >= working_hours.end_time:
                continue
            # Рабочий день ещё идёт, но оставшегося времени не хватает даже на самую короткую услугу — скрываем.
            if not await _today_has_free_time(booking_service, candidate, now):
                continue
        dates.append(candidate)
    return dates


async def _find_first_dates_page(
    schedule_repo: ScheduleRepository, booking_service: BookingService, tz: dt.tzinfo
) -> tuple[int, list[dt.date]] | None:
    for page in range(DATES_PAGES):
        dates = await _find_working_dates(schedule_repo, booking_service, tz, page)
        if dates:
            return page, dates
    return None


async def _show_dates_page(
    callback: CallbackQuery,
    state: FSMContext,
    schedule_repo: ScheduleRepository,
    booking_service: BookingService,
    tz: dt.tzinfo,
    page: int,
) -> None:
    page = min(max(page, 0), DATES_PAGES - 1)
    dates = await _find_working_dates(schedule_repo, booking_service, tz, page)
    await state.update_data(dates_page=page)
    header = "🕐 Выберите дату"
    text = header if dates else f"{header}\n\nВ этот период рабочих дней нет."
    try:
        await callback.message.edit_text(
            text, reply_markup=dates_keyboard(dates, page=page, total_pages=DATES_PAGES, with_back=False)
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
    await callback.answer()


@router.message(F.text == "🕐 Проверить время")
async def start_check_time(message: Message, state: FSMContext, session: AsyncSession) -> None:
    tz = await get_master_tz(session)
    schedule_repo = ScheduleRepository(session)
    found = await _find_first_dates_page(schedule_repo, BookingService(session, tz), tz)
    if found is None:
        await message.answer("Рабочие дни пока не настроены.")
        return
    page, dates = found

    await state.update_data(dates_page=page)
    await state.set_state(CheckTimeFSM.select_date)
    await message.answer(
        "🕐 Выберите дату", reply_markup=dates_keyboard(dates, page=page, total_pages=DATES_PAGES, with_back=False)
    )


@router.callback_query(CheckTimeFSM.select_date, DatesPageCallback.filter())
async def change_dates_page(
    callback: CallbackQuery, callback_data: DatesPageCallback, state: FSMContext, session: AsyncSession
) -> None:
    tz = await get_master_tz(session)
    await _show_dates_page(
        callback, state, ScheduleRepository(session), BookingService(session, tz), tz, callback_data.page
    )


@router.callback_query(CheckTimeFSM.select_date, DateCallback.filter())
async def select_date(
    callback: CallbackQuery, callback_data: DateCallback, state: FSMContext, session: AsyncSession
) -> None:
    tz = await get_master_tz(session)
    booking_service = BookingService(session, tz)
    selected_date = dt.date.fromisoformat(callback_data.date)

    working_hours, free_intervals, booking_ranges = await booking_service.get_day_schedule(selected_date)
    date_label = selected_date.strftime("%d.%m.%Y")

    # Записей на этот день ещё нет — сообщаем об этом текстом, под которым та же inline-кнопка «Записаться».
    has_valid_hours = (
        working_hours is not None
        and working_hours.is_working
        and working_hours.start_time is not None
        and working_hours.end_time is not None
    )
    if not booking_ranges or not has_valid_hours:
        await callback.message.answer(
            f"📅 {date_label}\nНа этот день пока нет записей.",
            reply_markup=start_booking_inline_keyboard(selected_date),
        )
        await callback.answer()
        return

    image_bytes = render_day_schedule_image(
        selected_date, tz, working_hours.start_time, working_hours.end_time, free_intervals
    )
    await callback.message.answer_photo(
        BufferedInputFile(image_bytes, filename=f"schedule_{selected_date.isoformat()}.png"),
        reply_markup=start_booking_inline_keyboard(selected_date),
    )
    await callback.answer()


@router.callback_query(ScheduleBookCallback.filter(F.action == "ask"))
async def ask_which_day_to_book(callback: CallbackQuery, callback_data: ScheduleBookCallback) -> None:
    """«Записаться» под расписанием: вместо кнопки показываем «Другой день» (слева) и «Этот день» (справа)."""
    try:
        day = dt.date.fromisoformat(callback_data.date)
    except ValueError:
        await callback.answer()
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=booking_day_choice_keyboard(day))
    except TelegramBadRequest as exc:
        # Двойное нажатие: клавиатура уже заменена — это не ошибка.
        if "message is not modified" not in str(exc):
            raise
    await callback.answer()


@router.callback_query(CheckTimeFSM.select_date, NavCallback.filter(F.target == "back"))
async def back_to_info_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.delete()
    await callback.message.answer(
        "ℹ️ Выберите действие", reply_markup=info_menu_keyboard(is_admin=is_admin_id(callback.from_user.id))
    )
    await callback.answer()
