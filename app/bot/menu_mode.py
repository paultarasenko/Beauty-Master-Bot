"""Запоминает, в каком меню сейчас находится админ: в админ-панели или в клиентском режиме.

Кнопки «ℹ️ Информация», «⬅️ Назад» и некоторые другие выглядят одинаково и в админ-панели, и в
клиентском меню, а Telegram присылает боту только текст нажатой кнопки. Чтобы понять, какое
подменю показать и куда вернуть, бот помнит последнее показанное админу главное меню.

Раньше это хранилось в set() в памяти процесса — и слеталось при каждом рестарте/деплое бота.
Проблема была в том, что reply-клавиатура остаётся у админа на экране и после рестарта (Telegram
её не трогает), поэтому админ продолжал видеть кнопки админ-панели, но бот уже считал его в
клиентском режиме — и «Информация» открывала клиентское подменю вместо админского.

Теперь режим хранится в БД (users.is_in_admin_panel) и переживает рестарт бота.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.user_repository import UserRepository


async def enter_admin_panel(session: AsyncSession, telegram_id: int) -> None:
    await UserRepository(session).set_admin_panel_mode(telegram_id, True)


async def leave_admin_panel(session: AsyncSession, telegram_id: int) -> None:
    await UserRepository(session).set_admin_panel_mode(telegram_id, False)


async def is_in_admin_panel(session: AsyncSession, telegram_id: int) -> bool:
    return await UserRepository(session).is_in_admin_panel(telegram_id)
