import logging

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import ChatMemberUpdated

from app.bot.middlewares.admin_filter import is_admin_id
from app.services.notifier import MasterNotifier

logger = logging.getLogger(__name__)

router = Router(name="client_bot_blocked")


@router.my_chat_member(F.chat.type == ChatType.PRIVATE, F.new_chat_member.status == ChatMemberStatus.KICKED)
async def client_blocked_bot(event: ChatMemberUpdated) -> None:
    """Клиент заблокировал бота (Telegram присылает это как my_chat_member со статусом «kicked»): сообщаем мастеру.

    Одного лишь удаления чата Telegram боту не сообщает — только блокировку, поэтому «удалил и заблокировал»
    приходит именно как блокировка."""
    if is_admin_id(event.from_user.id):
        return  # мастер сам заблокировал бота — уведомлять некого
    await MasterNotifier(event.bot).notify_client_blocked_bot(event.from_user)
