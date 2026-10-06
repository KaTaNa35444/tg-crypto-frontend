import os
from dataclasses import replace
import pytest_asyncio
from hunter.config import Settings
from hunter.models import Base,database
from hunter.service import Service

@pytest_asyncio.fixture
async def service():
    # TEST_DATABASE_URL must point at a disposable DB: this fixture drops its schema.
    url=os.getenv('TEST_DATABASE_URL','sqlite+aiosqlite:///:memory:')
    engine,sessions=database(url)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
    settings=Settings(url,'',frozenset({1}),False,900,False,24)
    yield Service(sessions,settings)
    await engine.dispose()
