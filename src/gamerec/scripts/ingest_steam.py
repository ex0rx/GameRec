import asyncio

from gamerec.db import SessionLocal
from gamerec.services.steam_ingestion import ingest_steam_catalogue


async def main() -> None:
    async with SessionLocal() as db:
        total = await ingest_steam_catalogue(
            db=db,
            page_size=1000,
            max_pages=None,
        )

    print(f"Total ingested: {total}")    


if __name__ == "__main__":
    asyncio.run(main())