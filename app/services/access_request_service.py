import datetime as dt
import logging

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AccessRequestStatus, ClientAccessRequest, User
from app.services.limit_request_service import burn_unused_approvals

logger = logging.getLogger(__name__)

BLOCKED_TEXT = (
    "🔒 Доступ к боту для вас ограничен.\n\n"
    "Запись, отзывы и обращения к мастеру через бота недоступны."
)
OPENED_TEXT = (
    "✅ Мастер открыл вам доступ к записи.\n\n"
    "Вы снова можете записываться — нажмите «📅 Записаться»."
)
PENDING_TEXT = "Ваше сообщение уже передано мастеру. Пожалуйста, дождитесь его решения."


async def get_pending_access_request(session: AsyncSession, user_id: int) -> ClientAccessRequest | None:
    result = await session.execute(
        select(ClientAccessRequest).where(
            ClientAccessRequest.user_id == user_id,
            ClientAccessRequest.status == AccessRequestStatus.PENDING.value,
        )
    )
    return result.scalars().first()


async def create_access_request(session: AsyncSession, user_id: int, message: str) -> ClientAccessRequest | None:
    """None — у клиента уже есть ожидающее обращение (в том числе при гонке двойной отправки)."""
    if await get_pending_access_request(session, user_id) is not None:
        return None
    request = ClientAccessRequest(user_id=user_id, message=message)
    session.add(request)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return None
    return request


async def decide_access_request(
    session: AsyncSession, request_id: int, open_access: bool
) -> tuple[ClientAccessRequest, User] | None:
    """Решение мастера. None — обращения нет или оно уже обработано (повторное нажатие кнопки)."""
    request = await session.get(ClientAccessRequest, request_id, with_for_update=True)
    if request is None or request.status != AccessRequestStatus.PENDING.value:
        return None
    user = await session.get(User, request.user_id)
    if user is None:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    if open_access:
        # Как «Подтвердить доступ» в предупреждении об отменах: снимаем ограничение и обнуляем серию отмен.
        user.is_restricted = False
        user.is_blocked_forever = False
        user.cancel_streak_reset_at = now
        user.over_limit_cancels = None
        request.status = AccessRequestStatus.OPENED.value
    else:
        user.is_restricted = True
        user.is_blocked_forever = True
        user.over_limit_cancels = None
        await burn_unused_approvals(session, user.id)
        request.status = AccessRequestStatus.BLOCKED.value
    request.decided_at = now
    await session.flush()
    return request, user


async def notify_client(bot: Bot, client: User, text: str) -> bool:
    try:
        await bot.send_message(client.telegram_id, text)
    except Exception:
        logger.exception("Не удалось отправить клиенту решение мастера (user_id=%s)", client.id)
        return False
    return True
