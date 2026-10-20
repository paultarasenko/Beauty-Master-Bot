from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.callback_data import ReminderActionCallback


def reminder_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Всё в силе",
                    callback_data=ReminderActionCallback(booking_id=booking_id, action="still_valid").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Перенести",
                    callback_data=ReminderActionCallback(booking_id=booking_id, action="reschedule").pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=ReminderActionCallback(booking_id=booking_id, action="cancel").pack(),
                ),
            ],
        ]
    )
