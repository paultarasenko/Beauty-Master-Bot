from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings


def make_engine(database_url: str | None = None):
    return create_async_engine(database_url or settings.database_url, pool_pre_ping=True)


engine = make_engine()
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def make_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Отдельная session factory — используется в тестах с тестовой БД."""
    test_engine = make_engine(database_url)
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
