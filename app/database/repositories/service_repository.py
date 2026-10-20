from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Service


class ServiceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active(self) -> list[Service]:
        result = await self.session.execute(
            select(Service).where(Service.active.is_(True)).order_by(Service.sort_order, Service.id)
        )
        return list(result.scalars().all())

    async def get_all(self) -> list[Service]:
        result = await self.session.execute(select(Service).order_by(Service.sort_order, Service.id))
        return list(result.scalars().all())

    async def get_by_id(self, service_id: int) -> Service | None:
        return await self.session.get(Service, service_id)
