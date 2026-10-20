from sqlalchemy import Boolean, CheckConstraint, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, TimestampMixin


class Service(Base, TimestampMixin):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_services_duration_positive"),
        CheckConstraint("price >= 0", name="ck_services_price_non_negative"),
        CheckConstraint("buffer_minutes >= 0", name="ck_services_buffer_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # «Свободное время» после услуги — подготовка к следующему клиенту. Входит в рабочие часы мастера,
    # но для записи недоступно: следующий клиент не может начаться раньше, чем закончится услуга + буфер.
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @property
    def occupied_minutes(self) -> int:
        """Сколько минут услуга занимает в расписании: длительность + свободное время после неё."""
        return self.duration_minutes + (self.buffer_minutes or 0)
