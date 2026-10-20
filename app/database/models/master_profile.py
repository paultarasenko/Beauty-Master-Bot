from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, TimestampMixin


class MasterProfile(Base, TimestampMixin):
    """Одна строка — текущая система рассчитана на одного мастера.

    Сущность выделена отдельно (а не захардкожена в .env), чтобы в будущем
    было легко перейти к нескольким мастерам без миграции бизнес-логики.
    """

    __tablename__ = "master_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # None — имя удалено мастером
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    greeting_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # None — стандартное приветствие
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    location_landmark: Mapped[str | None] = mapped_column(String(500), nullable=True)
    map_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contact_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    experience_years: Mapped[int | None] = mapped_column(nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(500), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Minsk")