import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BlockedPeriod, BreakPeriod, WorkingHours


class ScheduleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_working_hours(self, weekday: int) -> WorkingHours | None:
        result = await self.session.execute(select(WorkingHours).where(WorkingHours.weekday == weekday))
        return result.scalar_one_or_none()

    async def get_breaks(self, weekday: int) -> list[BreakPeriod]:
        result = await self.session.execute(
            select(BreakPeriod).where(BreakPeriod.weekday == weekday).order_by(BreakPeriod.start_time)
        )
        return list(result.scalars().all())

    async def get_blocked_periods(
        self, range_start: dt.datetime, range_end: dt.datetime
    ) -> list[BlockedPeriod]:
        result = await self.session.execute(
            select(BlockedPeriod).where(
                BlockedPeriod.start_datetime < range_end,
                BlockedPeriod.end_datetime > range_start,
            )
        )
        return list(result.scalars().all())

    async def get_upcoming_blocked_periods(self, now: dt.datetime, limit: int) -> list[BlockedPeriod]:
        """Блокировки, которые ещё не закончились, — ближайшие первыми."""
        result = await self.session.execute(
            select(BlockedPeriod)
            .where(BlockedPeriod.end_datetime > now)
            .order_by(BlockedPeriod.start_datetime, BlockedPeriod.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_blocked_period(self, block_id: int) -> BlockedPeriod | None:
        return await self.session.get(BlockedPeriod, block_id)

    async def delete_blocked_period(self, blocked_period: BlockedPeriod) -> None:
        await self.session.delete(blocked_period)
        await self.session.flush()

    async def upsert_working_hours(
        self, weekday: int, is_working: bool, start_time: dt.time | None, end_time: dt.time | None
    ) -> WorkingHours:
        wh = await self.get_working_hours(weekday)
        if wh is None:
            wh = WorkingHours(weekday=weekday, is_working=is_working, start_time=start_time, end_time=end_time)
            self.session.add(wh)
        else:
            wh.is_working = is_working
            wh.start_time = start_time
            wh.end_time = end_time
        await self.session.flush()
        return wh

    async def add_break(self, weekday: int, start_time: dt.time, end_time: dt.time) -> BreakPeriod:
        br = BreakPeriod(weekday=weekday, start_time=start_time, end_time=end_time)
        self.session.add(br)
        await self.session.flush()
        return br

    async def add_blocked_period(
        self, start_datetime: dt.datetime, end_datetime: dt.datetime, reason: str | None
    ) -> BlockedPeriod:
        bp = BlockedPeriod(start_datetime=start_datetime, end_datetime=end_datetime, reason=reason)
        self.session.add(bp)
        await self.session.flush()
        return bp