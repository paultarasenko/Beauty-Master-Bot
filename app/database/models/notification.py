import datetime as dt
import enum

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, TimestampMixin


class NotificationType(str, enum.Enum):
    REMINDER_24H = "reminder_24h"
    REMINDER_2H_CLIENT = "reminder_2h_client"
    REMINDER_2H_MASTER = "reminder_2h_master"


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        # На одну запись — только одно напоминание данного типа. Защита от дублей
        # на уровне БД, а не "будем аккуратны в коде".
        UniqueConstraint("booking_id", "type", name="uq_notifications_booking_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False, default=NotificationType.REMINDER_24H.value)
    scheduled_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=NotificationStatus.PENDING.value)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
