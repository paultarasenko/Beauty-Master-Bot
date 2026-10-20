from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MasterProfile, PortfolioItem, Review


class ContentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_master_profile(self) -> MasterProfile | None:
        result = await self.session.execute(select(MasterProfile).limit(1))
        return result.scalar_one_or_none()

    async def get_active_portfolio(self, category: str | None = None) -> list[PortfolioItem]:
        query = select(PortfolioItem).where(PortfolioItem.active.is_(True))
        if category:
            query = query.where(PortfolioItem.category == category)
        query = query.order_by(PortfolioItem.sort_order, PortfolioItem.id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_portfolio_item(self, item_id: int) -> PortfolioItem | None:
        result = await self.session.execute(select(PortfolioItem).where(PortfolioItem.id == item_id))
        return result.scalar_one_or_none()

    async def get_active_reviews(self) -> list[Review]:
        result = await self.session.execute(
            select(Review).where(Review.active.is_(True)).order_by(Review.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_reviews_stats(self) -> tuple[float, int]:
        """Средний рейтинг и количество активных отзывов одним запросом (без загрузки всех строк)."""
        result = await self.session.execute(
            select(func.avg(Review.rating), func.count(Review.id)).where(Review.active.is_(True))
        )
        average, count = result.one()
        return (float(average) if average is not None else 0.0, count or 0)

    async def get_reviews_page(self, offset: int, limit: int) -> list[Review]:
        result = await self.session.execute(
            select(Review)
            .where(Review.active.is_(True))
            .order_by(Review.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_review_by_client_id(self, client_id: int) -> Review | None:
        result = await self.session.execute(
            select(Review)
            .where(Review.client_id == client_id, Review.active.is_(True))
            .order_by(Review.created_at.desc())
        )
        # first(), а не scalar_one_or_none(): проверка «уже есть отзыв» и вставка не защищены
        # уникальным constraint'ом на уровне БД, поэтому при гонке (двойной быстрый тап) теоретически
        # может появиться больше одной строки — не должны падать, просто берём последнюю.
        return result.scalars().first()

    async def get_review_by_id(self, review_id: int) -> Review | None:
        result = await self.session.execute(select(Review).where(Review.id == review_id))
        return result.scalar_one_or_none()
