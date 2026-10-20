from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        """Пользователь с таким username (без учёта регистра, как в Telegram).

        Сравнение точное: `ilike` здесь нельзя — в нём `_` и `%` это подстановочные символы, а `_` в username
        встречается постоянно (`anna_k` совпало бы с `annaxk`).

        username в БД не уникален: если человек сменил ник, а ник занял другой, у старой строки он остаётся до
        её следующего обновления. Поэтому при нескольких совпадениях берём самую свежо обновлённую строку,
        а не падаем с MultipleResultsFound.
        """
        result = await self.session.execute(
            select(User)
            .where(func.lower(User.username) == username.lower())
            .order_by(User.updated_at.desc(), User.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> User:
        """Вызывается на каждый /start. Не создаёт дубликаты, обновляет данные если юзер уже есть."""
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            self.session.add(user)
            await self.session.flush()
            return user

        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        await self.session.flush()
        return user

    async def set_admin_panel_mode(self, telegram_id: int, value: bool) -> None:
        """Персистентно сохраняет, в каком меню сейчас админ (панель мастера / клиентский режим).

        Вызывается и до первого /start (например, если админ сразу отправил /admin), поэтому
        сам создаёт строку пользователя при необходимости — без имени/username, они допишутся
        следующим /start через get_or_create.
        """
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, is_in_admin_panel=value)
            self.session.add(user)
        else:
            user.is_in_admin_panel = value
        await self.session.flush()

    async def is_in_admin_panel(self, telegram_id: int) -> bool:
        user = await self.get_by_telegram_id(telegram_id)
        return user.is_in_admin_panel if user is not None else False