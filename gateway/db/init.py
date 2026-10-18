"""Create (or with --reset, recreate) the schema: `uv run python -m gateway.db.init [--reset]`."""

import asyncio
import sys

from gateway.config import get_settings
from gateway.db.session import create_schema, make_engine


async def main(reset: bool) -> None:
    engine = make_engine(get_settings().database_url)
    await create_schema(engine, drop=reset)
    await engine.dispose()
    print("schema reset" if reset else "schema ready")


if __name__ == "__main__":
    asyncio.run(main("--reset" in sys.argv))
