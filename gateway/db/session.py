from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from gateway.db.models import SCHEMA, Base
from gateway.hosting import db_url


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(db_url(url), pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_schema(engine: AsyncEngine, drop: bool = False) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        await _move_legacy_tables(conn)
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await add_missing_columns(conn, Base.metadata, SCHEMA)


async def _move_legacy_tables(conn) -> None:
    """Tables created before the two-schema split lived in `public`; move them, data included."""
    for table in Base.metadata.sorted_tables:
        name = table.name
        legacy = (await conn.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{name}"})).scalar()
        current = (await conn.execute(text("SELECT to_regclass(:n)"), {"n": f"{SCHEMA}.{name}"})).scalar()
        if legacy and not current:
            await conn.execute(text(f"ALTER TABLE public.{name} SET SCHEMA {SCHEMA}"))


async def add_missing_columns(conn, metadata, schema: str) -> None:
    """Minimal forward migration until Alembic: add new nullable columns to existing tables."""
    for table in metadata.sorted_tables:
        rows = await conn.execute(
            text("SELECT column_name FROM information_schema.columns "
                 "WHERE table_schema = :s AND table_name = :t"),
            {"s": schema, "t": table.name},
        )
        existing = {r[0] for r in rows}
        for col in table.columns:
            if col.name not in existing and col.nullable:
                ddl = col.type.compile(dialect=conn.dialect)
                await conn.execute(text(
                    f'ALTER TABLE {schema}.{table.name} ADD COLUMN IF NOT EXISTS "{col.name}" {ddl}'))
