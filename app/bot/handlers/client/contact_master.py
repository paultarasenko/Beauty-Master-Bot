from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client_keyboards import master_request_cancel_keyboard
from app.bot.middlewares.admin_filter import is_admin_id
from app.bot.states.booking_states import ContactMasterFSM
from app.database.repositories.user_repository import UserRepository
from app.services.access_request_service import (
    BLOCKED_TEXT,
    PENDING_TEXT,
    create_access_request,
    get_pending_access_request,
)
from app.services.limit_request_service import MAX_REQUEST_LEN
from app.services.notifier import MasterNotifier

router = Router(name="client_contact_master")


@router.message(F.text == "📞 Связь с мастером")
async def contact_master(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Для ограниченного клиента «Связь с мастером» — это одно сообщение мастеру прямо в боте.
    Для всех остальных кнопка работает как раньше (ссылка на мастера) — её обрабатывает client/content.py."""
    user = await UserRepository(session).get_by_telegram_id(message.from_user.id)
    if is_admin_id(message.from_user.id) or user is None or not (user.is_restricted or user.is_blocked_forever):
        raise SkipHandler
    if user.is_blocked_forever:
        await message.answer(BLOCKED_TEXT)
        return
    if await get_pending_access_request(session, user.id) is not None:
        await message.answer(PENDING_TEXT)
        return

    await state.set_state(ContactMasterFSM.waiting_message)
    await message.answer(
        "✉️ Напишите сообщение мастеру.\n\n"
        "Отправить его можно один раз: мастер рассмотрит обращение и примет решение.",
        reply_markup=master_request_cancel_keyboard(),
    )


@router.message(ContactMasterFSM.waiting_message, F.text)
async def receive_message(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = message.text.strip()
    if not text:
        await message.answer("Сообщение не должно быть пустым. Напишите текст и отправьте ещё раз.")
        return
    if len(text) > MAX_REQUEST_LEN:
        await message.answer(
            f"Сообщение слишком длинное: {len(text)} символов, максимум {MAX_REQUEST_LEN}. "
            "Сократите его и отправьте ещё раз."
        )
        return

    user = await UserRepository(session).get_by_telegram_id(message.from_user.id)
    if user is None:
        await state.clear()
        return
    if user.is_blocked_forever:
        await state.clear()
        await message.answer(BLOCKED_TEXT)
        return

    request = await create_access_request(session, user.id, text)
    if request is None:
        await state.clear()
        await message.answer(PENDING_TEXT)
        return
    await session.commit()

    if not await MasterNotifier(message.bot).notify_access_request(request, user):
        # Мастер не получил обращение — не оставляем его «висеть», иначе клиент не сможет написать ещё раз.
        await session.delete(request)
        await session.commit()
        await state.clear()
        await message.answer("Не удалось передать сообщение мастеру. Попробуйте позже.")
        return

    await state.clear()
    await message.answer("✅ Сообщение передано мастеру. Ответ придёт в этот чат.")


@router.message(ContactMasterFSM.waiting_message)
async def receive_non_text(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте сообщение текстом.")
