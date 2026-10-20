import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Booking, BookingStatus, Notification, NotificationStatus

logger = logging.getLogger(__name__)


class ReminderService:
    """Находит due-напоминания и отправляет их. Идемпотентно — safe при повторном запуске
    после рестарта, потому что источник истины — таблица notifications, а не память процесса.
    """

    def __init__(self, session: AsyncSession, send_message_func, send_funcs_by_type: dict | None = None):
        """send_message_func(booking: Booking) -> Awaitable[None]
        Функция реальной отправки в Telegram — передаётся снаружи, чтобы сервис
        был тестируемым без реального Bot API.

        send_funcs_by_type — необязательный словарь {тип уведомления: функция отправки}
        для уведомлений, которые уходят не по умолчанию (например, напоминание мастеру).
        Для типов, которых в словаре нет, используется send_message_func.
        """
        self.session = session
        self.send_message_func = send_message_func
        self.send_funcs_by_type = send_funcs_by_type or {}

    async def get_due_notifications(self, now: dt.datetime) -> list[Notification]:
        result = await self.session.execute(
            select(Notification).where(
                Notification.status == NotificationStatus.PENDING.value,
                Notification.scheduled_at <= now,
            )
        )
        return list(result.scalars().all())

    async def process_due_notifications(self, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now(dt.timezone.utc)
        due = await self.get_due_notifications(now)
        sent_count = 0

        for notification in due:
            # Атомарно "забираем" уведомление: UPDATE ... WHERE status='pending' AND id=...
            # с проверкой rowcount — если два воркера одновременно подхватят одно и то же
            # уведомление, второй апдейт не найдёт строк и не отправит дубль.
            from sqlalchemy import update

            result = await self.session.execute(
                update(Notification)
                .where(Notification.id == notification.id, Notification.status == NotificationStatus.PENDING.value)
                .values(status=NotificationStatus.SENT.value, sent_at=now)
            )
            await self.session.flush()
            if result.rowcount == 0:
                continue  # кто-то другой уже забрал это уведомление

            booking = await self.session.get(Booking, notification.booking_id)
            if booking is None or booking.status != BookingStatus.CONFIRMED.value:
                # Запись отменена/удалена между планированием и отправкой — не шлём.
                continue

            try:
                send_func = self.send_funcs_by_type.get(notification.type, self.send_message_func)
                await send_func(booking)
                sent_count += 1
            except Exception:
                logger.exception("Failed to send reminder for booking %s", booking.id)
                notification.status = NotificationStatus.FAILED.value
                await self.session.flush()

        await self.session.commit()
        return sent_count
