import asyncio
import contextlib
from aiogram import Bot,Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from hunter.config import Settings
from hunter.models import database
from hunter.service import Service
from hunter.bot import router
from hunter.adapters import ADAPTERS
from hunter.worker import Outbox
from hunter.demo import run_demo

async def poll_sources(service):
    adapters=[cls() for cls in ADAPTERS.values()]
    while True:
        for adapter in adapters:
            try:
                items=await adapter.collect()
                await service.ingest_batch(adapter.exchange,items,adapter.verified)
            except Exception as exc:
                # Only curated messages; never log source URLs, credentials or response bodies.
                from hunter.adapters import SourceUnavailable
                error=str(exc) if isinstance(exc,SourceUnavailable) else type(exc).__name__
                await service.source_error(adapter.exchange,error)
        await asyncio.sleep(service.settings.poll_seconds)

async def reminders(service):
    while True:
        await service.schedule_reminders()
        await asyncio.sleep(30)

async def main():
    settings=Settings.load()
    from hunter.logging_safe import configure
    configure(settings)
    engine,sessions=database(settings.database_url)
    service=Service(sessions,settings)
    try:
        if settings.demo:
            await run_demo(service)
            return
        if not settings.bot_token: raise RuntimeError('BOT_TOKEN is missing; put it in local .env, never in chat/git')
        if not settings.admin_ids: raise RuntimeError('ADMIN_IDS is required for moderation')
        bot=Bot(settings.bot_token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp=Dispatcher(); dp.include_router(router(service))
        async def send(uid,text,**kwargs):
            await bot.send_message(uid,text,disable_web_page_preview=True,**kwargs)
        tasks=[asyncio.create_task(poll_sources(service)),asyncio.create_task(reminders(service)),
               asyncio.create_task(Outbox(sessions,settings).run(send)),asyncio.create_task(dp.start_polling(bot,allowed_updates=['message','callback_query']))]
        try:
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done: task.result()
        finally:
            for task in tasks: task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError): await task
            await bot.session.close()
    finally: await engine.dispose()

if __name__=='__main__':
    import sys
    try: asyncio.run(main())
    except Exception as exc:
        # Never dump exceptions, SQL parameters or Telegram request URLs.
        print('Startup/runtime failed: '+type(exc).__name__+'. Check DB, BOT_TOKEN, ADMIN_IDS and source status.',file=sys.stderr)
        sys.exit(1)
