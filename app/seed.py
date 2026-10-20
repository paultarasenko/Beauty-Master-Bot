"""Первичное заполнение БД тестовыми данными.

Запуск: python -m app.seed

Не запускается автоматически при старте приложения — только руками, один раз
(или повторно, идемпотентно проверяет наличие данных перед вставкой).
"""

import asyncio
import datetime as dt

from sqlalchemy import select

from app.database.models import MasterProfile, Service, WorkingHours
from app.database.session import async_session_factory


async def seed() -> None:
    async with async_session_factory() as session:
        existing_profile = (await session.execute(select(MasterProfile))).scalar_one_or_none()
        if existing_profile is None:
            session.add(
                MasterProfile(
                    name="Анна Иванова",
                    description="Мастер маникюра и педикюра с 5-летним опытом.",
                    address="ул. Лобанка, 42, Минск",
                    location_landmark="Рядом с ТЦ Origo",
                    contact_username="anna_beauty",
                    experience_years=5,
                    specialization="Маникюр, покрытие гель-лак, дизайн ногтей",
                    timezone="Europe/Minsk",
                )
            )
            print("✓ master_profile создан")
        else:
            print("• master_profile уже существует, пропускаю")

        existing_services = (await session.execute(select(Service))).scalars().first()
        if existing_services is None:
            session.add_all(
                [
                    Service(
                        name="Маникюр классический",
                        description="Классический маникюр без покрытия.",
                        price=25,
                        duration_minutes=45,
                        active=True,
                        sort_order=0,
                    ),
                    Service(
                        name="Маникюр + гель-лак",
                        description="Маникюр с покрытием гель-лаком.",
                        price=40,
                        duration_minutes=60,
                        active=True,
                        sort_order=1,
                    ),
                    Service(
                        name="Наращивание ногтей",
                        description="Наращивание с дизайном.",
                        price=60,
                        duration_minutes=90,
                        active=True,
                        sort_order=2,
                    ),
                ]
            )
            print("✓ тестовые услуги добавлены (3 шт.)")
        else:
            print("• услуги уже существуют, пропускаю")

        existing_wh = (await session.execute(select(WorkingHours))).scalars().first()
        if existing_wh is None:
            # Пн-Пт 10:00-19:00, Сб 10:00-15:00, Вс выходной
            for weekday in range(5):
                session.add(
                    WorkingHours(weekday=weekday, is_working=True, start_time=dt.time(10, 0), end_time=dt.time(19, 0))
                )
            session.add(
                WorkingHours(weekday=5, is_working=True, start_time=dt.time(10, 0), end_time=dt.time(15, 0))
            )
            session.add(WorkingHours(weekday=6, is_working=False, start_time=None, end_time=None))
            print("✓ базовое расписание добавлено (Пн-Пт 10-19, Сб 10-15, Вс выходной)")
        else:
            print("• расписание уже существует, пропускаю")

        await session.commit()
    print("\nГотово.")


if __name__ == "__main__":
    asyncio.run(seed())