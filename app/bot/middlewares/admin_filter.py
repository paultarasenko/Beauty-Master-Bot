import logging

from typing import Union

from aiogram import Bot
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.config.settings import settings

logger = logging.getLogger(__name__)


def is_admin_id(telegram_id: int | None) -> bool:
    """Используется и фильтром ниже, и хендлерами, которым нужно решить это без DI-фильтра
    (например, чтобы показать/скрыть кнопку переключения в клиентскую/админскую клавиатуру)."""
    return telegram_id is not None and telegram_id == settings.admin_telegram_id


async def get_admin_contact_username(bot: Bot) -> str | None:
    """Username админа (для кнопки-ссылки на чат с ним), определяется по ADMIN_TELEGRAM_ID из .env,
    а не хранится отдельно в БД — так кнопка всегда ведёт на того, кто сейчас назначен админом.
    Если у аккаунта нет username или чат ещё недоступен боту, возвращает None — кнопку тогда не показываем.
    """
    try:
        chat = await bot.get_chat(settings.admin_telegram_id)
    except Exception:
        logger.exception("Не удалось получить chat админа (admin_telegram_id=%s)", settings.admin_telegram_id)
        return None
    return chat.username


class IsAdminFilter(BaseFilter):
    """Пропускает апдейт дальше только если это ADMIN_TELEGRAM_ID из конфига.
    Работает и для Message, и для CallbackQuery — применяется на router.message
    И router.callback_query каждого admin-роутера, иначе callback-хендлеры
    (инлайн-кнопки в админке) остаются доступны кому угодно, кто пришлёт нужный callback_data.
    """

    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        return event.from_user is not None and is_admin_id(event.from_user.id)
