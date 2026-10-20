import datetime as dt
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.admin_keyboards import schedule_start_time_keyboard
from app.bot.keyboards.callback_data import AdminScheduleCallback, ConfirmCallback, NavCallback, TimeCallback
from app.bot.keyboards.client_keyboards import confirm_keyboard, times_keyboard
from app.bot.middlewares.admin_filter import IsAdminFilter
from app.bot.states.booking_states import AdminScheduleFSM
from app.config.settings import settings
from app.database.models import WorkingHours
from app.database.repositories.booking_repository import BookingRepository
from app.database.repositories.schedule_repository import ScheduleRepository
from app.utils.timezone import get_master_tz, to_local

router = Router(name="admin_schedule")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())

WEEKDAY_NAMES_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

DAY_START = dt.time(5, 0)
DAY_END = dt.time(23, 0)  # последний слот сетки при шаге slot_interval_minutes от DAY_START


def _day_icon(wh: WorkingHours | None) -> str:
    """✅ — рабочий день, 🚫 — выходной (те же условия, что в _format_hours)."""
    working = wh is not None and wh.is_working and wh.start_time is not None and wh.end_time is not None
    return "✅" if working else "🚫"


def _weekday_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"wd:{i}")]
            for i, name in enumerate(WEEKDAY_NAMES_RU)
        ]
    )


async def _load_week_hours(session: AsyncSession) -> list[WorkingHours | None]:
    repo = ScheduleRepository(session)
    return [await repo.get_working_hours(i) for i in range(len(WEEKDAY_NAMES_RU))]


def _day_time_grid(tz: dt.tzinfo) -> list[dt.datetime]:
    """Все времена с шагом slot_interval_minutes между DAY_START и DAY_END — для выбора начала/конца
    рабочего дня (в отличие от блокировки времени, тут ещё нет сохранённых рабочих часов, на которые
    можно опереться)."""
    anchor = dt.date.today()
    cur = dt.datetime.combine(anchor, DAY_START, tzinfo=tz)
    end = dt.datetime.combine(anchor, DAY_END, tzinfo=tz)
    step = dt.timedelta(minutes=settings.slot_interval_minutes)
    grid: list[dt.datetime] = []
    while cur <= end:
        grid.append(cur)
        cur += step
    return grid


def _format_hours(wh: WorkingHours | None) -> str:
    """«10:00 — 19:00» или «выходной» — для списка недели и для экрана редактирования дня."""
    if wh is None or not wh.is_working or wh.start_time is None or wh.end_time is None:
        return "выходной"
    return f"{wh.start_time.strftime('%H:%M')} — {wh.end_time.strftime('%H:%M')}"


async def _weekday_prompt(session: AsyncSession, weekday: int) -> str:
    """Экран выбора времени начала. Под названием дня — сохранённые сейчас часы работы: мастер видит,
    что именно меняет. Берём их из БД, а не из незавершённого ввода, поэтому после «Назад»/«Изменить» тоже актуальны."""
    current = _format_hours(await ScheduleRepository(session).get_working_hours(weekday))
    return (
        f"{WEEKDAY_NAMES_RU[weekday]}.\n"
        f"🕐 Сейчас: {current}\n\n"
        "Выберите время начала работы, или сделайте день выходным"
    )


async def _find_day_off_conflicts(session: AsyncSession, tz: dt.tzinfo, weekday: int) -> list:
    """Подтверждённые записи на будущее, которые приходятся на этот день недели.

    День недели не привязан к одной дате, поэтому смотрим вообще все будущие подтверждённые
    записи и оставляем те, что попадают на нужный weekday в таймзоне мастера.
    """
    now = dt.datetime.now(tz)
    upcoming = await BookingRepository(session).get_confirmed_upcoming(now)
    return [b for b in upcoming if to_local(b.start_datetime, tz).weekday() == weekday]


@router.message(F.text == "🕐 Расписание")
async def show_schedule(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()  # если мастер не закончил редактирование другого дня — сбрасываем незавершённый ввод
    week_hours = await _load_week_hours(session)
    lines = ["🕐 Текущее расписание:\n"]
    for name, wh in zip(WEEKDAY_NAMES_RU, week_hours):
        lines.append(f"{_day_icon(wh)} {name}: {_format_hours(wh)}")
    lines.append("\nЧтобы изменить день, выберите его")
    await message.answer("\n".join(lines), reply_markup=_weekday_keyboard())


@router.callback_query(F.data.startswith("wd:"))
async def select_weekday(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    weekday = int(callback.data.split(":")[1])
    tz = await get_master_tz(session)
    await state.update_data(weekday=weekday)
    await state.set_state(AdminScheduleFSM.select_start_time)
    await callback.message.edit_text(await _weekday_prompt(session, weekday), reply_markup=schedule_start_time_keyboard(_day_time_grid(tz)))
    await callback.answer()


@router.callback_query(AdminScheduleFSM.select_start_time, NavCallback.filter(F.target == "back"))
async def back_to_weekdays(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("🕐 Выберите день, который нужно изменить:", reply_markup=_weekday_keyboard())
    await callback.answer()


@router.callback_query(AdminScheduleFSM.select_start_time, AdminScheduleCallback.filter(F.action == "day_off"))
async def set_day_off(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    weekday = data["weekday"]
    tz = await get_master_tz(session)

    conflicts = await _find_day_off_conflicts(session, tz, weekday)
    if conflicts:
        lines = "\n".join(
            f"• {to_local(b.start_datetime, tz).strftime('%d.%m %H:%M')} — {escape(b.service_name_snapshot)}"
            for b in conflicts
        )
        await state.set_state(AdminScheduleFSM.confirm_day_off)
        await callback.message.edit_text(
            f"⚠️ На {WEEKDAY_NAMES_RU[weekday].lower()} уже есть подтверждённые записи:\n\n"
            f"{lines}\n\n"
            "Они не будут отменены автоматически и останутся как есть, если сделать день выходным. "
            "Клиентам об этом ничего не придёт — отменить или перенести их нужно будет вручную "
            "в разделе «Записи».\n\n"
            "Всё равно сделать этот день выходным?",
            reply_markup=confirm_keyboard(),
        )
        await callback.answer()
        return

    await ScheduleRepository(session).upsert_working_hours(weekday, is_working=False, start_time=None, end_time=None)
    await session.commit()
    await state.clear()
    await callback.message.edit_text(f"✅ {WEEKDAY_NAMES_RU[weekday]} теперь выходной.")
    await callback.answer()


@router.callback_query(AdminScheduleFSM.confirm_day_off, ConfirmCallback.filter(F.action == "edit"))
async def cancel_day_off(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    tz = await get_master_tz(session)
    data = await state.get_data()
    weekday = data["weekday"]
    await state.set_state(AdminScheduleFSM.select_start_time)
    await callback.message.edit_text(await _weekday_prompt(session, weekday), reply_markup=schedule_start_time_keyboard(_day_time_grid(tz)))
    await callback.answer()


@router.callback_query(AdminScheduleFSM.confirm_day_off, ConfirmCallback.filter(F.action == "yes"))
async def confirm_day_off(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    weekday = data["weekday"]
    await ScheduleRepository(session).upsert_working_hours(weekday, is_working=False, start_time=None, end_time=None)
    await session.commit()
    await state.clear()
    await callback.message.edit_text(f"✅ {WEEKDAY_NAMES_RU[weekday]} теперь выходной.")
    await callback.answer()


@router.callback_query(AdminScheduleFSM.select_start_time, TimeCallback.filter())
async def select_start_time(
    callback: CallbackQuery, callback_data: TimeCallback, state: FSMContext, session: AsyncSession
) -> None:
    tz = await get_master_tz(session)
    start_dt = dt.datetime.fromtimestamp(callback_data.ts, tz=tz)
    options = [t for t in _day_time_grid(tz) if t.time() > start_dt.time()]
    if not options:
        await callback.answer("Для этого времени начала нет доступного времени окончания.", show_alert=True)
        return

    data = await state.get_data()
    weekday = data["weekday"]
    await state.update_data(start_time=start_dt.time().isoformat())
    await state.set_state(AdminScheduleFSM.select_end_time)
    await callback.message.edit_text(
        f"{WEEKDAY_NAMES_RU[weekday]}\n🕐 Начало: {start_dt.strftime('%H:%M')}\n\n🕐 Выберите время окончания работы:",
        reply_markup=times_keyboard(options, with_main_menu=False),
    )
    await callback.answer()


@router.callback_query(AdminScheduleFSM.select_end_time, NavCallback.filter(F.target == "back"))
async def back_to_start_time(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    tz = await get_master_tz(session)
    data = await state.get_data()
    weekday = data["weekday"]
    await state.set_state(AdminScheduleFSM.select_start_time)
    await callback.message.edit_text(await _weekday_prompt(session, weekday), reply_markup=schedule_start_time_keyboard(_day_time_grid(tz)))
    await callback.answer()


@router.callback_query(AdminScheduleFSM.select_end_time, TimeCallback.filter())
async def select_end_time(
    callback: CallbackQuery, callback_data: TimeCallback, state: FSMContext, session: AsyncSession
) -> None:
    tz = await get_master_tz(session)
    end_dt = dt.datetime.fromtimestamp(callback_data.ts, tz=tz)
    data = await state.get_data()
    weekday = data["weekday"]
    start_time = dt.time.fromisoformat(data["start_time"])

    await state.update_data(end_time=end_dt.time().isoformat())
    await state.set_state(AdminScheduleFSM.confirm)
    text = (
        "Сохранить расписание?\n\n"
        f"{WEEKDAY_NAMES_RU[weekday]}: {start_time.strftime('%H:%M')} — {end_dt.strftime('%H:%M')}"
    )
    await callback.message.edit_text(text, reply_markup=confirm_keyboard())
    await callback.answer()


@router.callback_query(AdminScheduleFSM.confirm, ConfirmCallback.filter(F.action == "edit"))
async def edit_schedule_time(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    tz = await get_master_tz(session)
    data = await state.get_data()
    weekday = data["weekday"]
    await state.set_state(AdminScheduleFSM.select_start_time)
    await callback.message.edit_text(await _weekday_prompt(session, weekday), reply_markup=schedule_start_time_keyboard(_day_time_grid(tz)))
    await callback.answer()


@router.callback_query(AdminScheduleFSM.confirm, ConfirmCallback.filter(F.action == "yes"))
async def confirm_schedule(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    weekday = data["weekday"]
    start_time = dt.time.fromisoformat(data["start_time"])
    end_time = dt.time.fromisoformat(data["end_time"])

    await ScheduleRepository(session).upsert_working_hours(
        weekday, is_working=True, start_time=start_time, end_time=end_time
    )
    await session.commit()
    await state.clear()
    await callback.message.edit_text(
        f"✅ {WEEKDAY_NAMES_RU[weekday]}: {start_time.strftime('%H:%M')} — {end_time.strftime('%H:%M')} сохранено."
    )
    await callback.answer()