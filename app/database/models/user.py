import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # В каком меню сейчас находится админ (панель мастера / клиентский режим). Хранится в БД,
    # а не в памяти процесса — иначе рестарт бота (или деплой) сбрасывает режим, а старая
    # reply-клавиатура остаётся на экране у админа, и нажатия на одинаковые кнопки («Информация»
    # и т.п.) в обеих клавиатурах начинают попадать не в то меню. См. app/bot/menu_mode.py.
    is_in_admin_panel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Мастер ограничил клиенту доступ: новые записи недоступны (уже созданные остаются).
    is_restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Мастер нажал «Ограничить навсегда»: клиент не может ни записываться, ни писать мастеру, ни оставлять отзывы.
    is_blocked_forever: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Серия отмен считается только по записям, созданным после этого момента: мастер нажал «Подтвердить доступ»,
    # и клиент начинает «с чистого листа», а не получает то же предупреждение при следующей отмене.
    cancel_streak_reset_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Клиент, у которого стало больше трёх записей (мастер дал разрешение сверх лимита и клиент им воспользовался),
    # «в состоянии сверх лимита»: считаем его отмены, любые, не обязательно подряд. NULL — клиент в этом состоянии
    # не находится. Три отмены — предупреждение мастеру; возврат в обычный лимит, решение мастера или предупреждение
    # обнуляют (NULL). Это точная отметка, а не вывод из «использованных разрешений»: сгоревшее разрешение её не ставит.
    over_limit_cancels: Mapped[int | None] = mapped_column(Integer, nullable=True)
