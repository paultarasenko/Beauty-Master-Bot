import asyncio
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MasterProfile, Service, User
from app.database.session import make_session_factory

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5434/beauty_bot_test"
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RIGA_TZ = dt.timezone(dt.timedelta(hours=2))  # фиксированный offset — достаточно для тестов бизнес-логики


def next_weekday(weekday: int, min_days_ahead: int = 14) -> dt.date:
    """Ближайший день недели `weekday` (0 — понедельник) не раньше чем через `min_days_ahead` дней.

    Тесты с БД создают записи через настоящие часы (`BookingService` сверяется с `datetime.now`),
    поэтому зашитая дата рано или поздно оказывается в прошлом, и тесты краснеют без всякого изменения кода.
    """
    target = dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=min_days_ahead)
    return target + dt.timedelta(days=(weekday - target.weekday()) % 7)

TABLES_TO_CLEAN = [
    "admin_reminders",
    "notifications",
    "bookings",
    "blocked_periods",
    "breaks",
    "working_hours",
    "services",
    "users",
    "master_profile",
]


def _check_test_database_is_safe() -> None:
    """Фикстуры делают TRUNCATE — запрещаем запуск против любой БД, имя которой не оканчивается на _test."""
    db_name = make_url(TEST_DATABASE_URL).database or ""
    if not db_name.endswith("_test"):
        pytest.exit(
            f"TEST_DATABASE_URL указывает на БД '{db_name}': тесты очищают таблицы, "
            "имя тестовой БД должно оканчиваться на '_test'.",
            returncode=2,
        )


async def _can_connect(dsn: str) -> None:
    conn = await asyncpg.connect(dsn, timeout=5)
    await conn.close()


@pytest.fixture(scope="session")
def _test_database() -> None:
    """Один раз за прогон (подключается фикстурой `session`, чистым unit-тестам БД не нужна): проверяет доступность тестовой БД и приводит схему к `alembic upgrade head`.

    Без БД падаем сразу и понятно, а не получаем десятки ConnectionRefusedError с долгими таймаутами.
    """
    _check_test_database_is_safe()

    asyncpg_dsn = make_url(TEST_DATABASE_URL).set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        asyncio.run(_can_connect(asyncpg_dsn))
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
        pytest.exit(
            f"Тестовая БД недоступна ({exc!r}).\n"
            "Поднимите её: docker compose -f docker-compose.test.yml up -d --wait\n"
            "или задайте свою через переменную окружения TEST_DATABASE_URL.",
            returncode=2,
        )

    # alembic/env.py берёт URL из settings.database_url (читает DATABASE_URL) — подменяем только для подпроцесса.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.exit(f"alembic upgrade head не выполнился:\n{result.stderr or result.stdout}", returncode=2)


@pytest_asyncio.fixture
async def session(_test_database):
    session_factory = make_session_factory(TEST_DATABASE_URL)
    async with session_factory() as s:
        yield s
        await s.rollback()
    # Чистим таблицы после каждого теста, чтобы тесты были независимы друг от друга.
    async with session_factory() as s:
        for table in TABLES_TO_CLEAN:
            await s.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        await s.commit()


@pytest_asyncio.fixture
async def master_profile(session: AsyncSession) -> MasterProfile:
    mp = MasterProfile(name="Test Master", timezone="Europe/Riga")
    session.add(mp)
    await session.flush()
    await session.commit()
    return mp


@pytest_asyncio.fixture
async def test_user(session: AsyncSession) -> User:
    user = User(telegram_id=111111, username="client1", first_name="Ivan")
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def test_user_2(session: AsyncSession) -> User:
    user = User(telegram_id=222222, username="client2", first_name="Maria")
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def manicure_service(session: AsyncSession) -> Service:
    service = Service(name="Маникюр", price=40, duration_minutes=60, active=True, sort_order=0)
    session.add(service)
    await session.flush()
    await session.commit()
    return service


@pytest_asyncio.fixture
async def long_service(session: AsyncSession) -> Service:
    """Услуга на 90 минут — для проверки edge cases с длинными процедурами."""
    service = Service(name="Наращивание", price=60, duration_minutes=90, active=True, sort_order=1)
    session.add(service)
    await session.flush()
    await session.commit()
    return service
