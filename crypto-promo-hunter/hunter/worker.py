import asyncio
from datetime import datetime,timedelta
from uuid import uuid4
from sqlalchemy import select, update, or_, and_
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest, TelegramNetworkError
from hunter.models import Job, User, Promotion, Subscription, Saved, aware
from hunter.schema import utcnow
from hunter.render import render_card, chunks, words

class Outbox:
    def __init__(self,sessions,settings): self.sessions,self.settings=sessions,settings

    async def claim(self):
        now=utcnow()
        async with self.sessions.begin() as s:
            job=await s.scalar(select(Job).where(Job.available_at<=now,
                or_(Job.status=='pending',and_(Job.status=='sending',Job.lease_until<now)))
                .order_by(Job.id).limit(1).with_for_update(skip_locked=True))
            if not job: return None
            job.status='sending'; job.lease_token=str(uuid4()); job.lease_until=now+timedelta(minutes=5)
            job.attempts+=1
            return job

    async def finish(self,job,status,error=None,delay=0):
        async with self.sessions.begin() as s:
            await s.execute(update(Job).where(Job.id==job.id,Job.lease_token==job.lease_token,
                Job.status=='sending').values(status=status,error=error,lease_token=None,lease_until=None,
                available_at=utcnow()+timedelta(seconds=delay)))

    async def deliverable(self,job):
        async with self.sessions() as s:
            u=await s.get(User,job.user_id); p=await s.get(Promotion,job.promotion_id)
            if job.kind=='review':
                if not self.settings.is_admin(job.user_id) or not p or not p.candidate: return None
                return u or User(id=job.user_id,language='ru',timezone='Europe/Kyiv'),p
            if not u or u.blocked or not u.notifications or not p or not p.data: return None
            if p.demo != self.settings.demo: return None
            if job.kind=='new':
                if p.status!='published': return None
                if not await s.get(Subscription,(u.id,p.exchange)): return None
            if job.kind in {'update','status'} and not await s.get(Saved,(u.id,p.id)): return None
            if job.kind in {'new','update'}:
                if p.status!='published' or (p.data.get('end_at') and datetime.fromisoformat(p.data['end_at'])<=utcnow()): return None
            if job.kind=='reminder':
                if p.status!='published' or p.candidate or p.data.get('end_at')!=job.expected_deadline: return None
                if datetime.fromisoformat(job.expected_deadline)<=utcnow(): return None
                if aware(p.checked_at)<utcnow()-timedelta(hours=self.settings.reminder_fresh_hours): return None
            return u,p

    async def process_one(self,sender):
        job=await self.claim()
        if job is None: return False
        pair=await self.deliverable(job)
        if pair is None:
            await self.finish(job,'cancelled'); return True
        u,p=pair
        title= 'Admin review /admin' if job.kind=='review' else words(u.language).get(job.kind,job.kind)
        try:
            # DEMO always writes to stdout; even a configured token cannot send demo jobs.
            text=title+'\n'+render_card(p,u.language,u.timezone,candidate=job.kind=='review')
            if self.settings.demo:
                print(f'[DEMO OUTBOX user={u.id}] {text}')
            else:
                parts=chunks(text)
                from hunter.keyboards import card_buttons, keyboard, button
                for index,part in enumerate(parts):
                    rows = ([[button('Admin /admin','admin_queue')]] if job.kind=='review'
                            else card_buttons(p,u.language,await self._saved(u.id,p.id)))
                    await sender(u.id,part,reply_markup=keyboard(rows) if index==len(parts)-1 else None)
                    await asyncio.sleep(0.05)
            await self.finish(job,'sent')
        except TelegramRetryAfter as exc:
            await self.finish(job,'pending','telegram_rate_limit',int(exc.retry_after)+1)
        except TelegramForbiddenError:
            async with self.sessions.begin() as s:
                user=await s.get(User,u.id)
                if user: user.blocked=True
            await self.finish(job,'cancelled','bot_blocked')
        except TelegramBadRequest:
            await self.finish(job,'failed','telegram_bad_request')
        except (TelegramNetworkError,TimeoutError,ConnectionError):
            await self.finish(job,'failed' if job.attempts>=8 else 'pending','network_error',min(3600,2**job.attempts))
        except Exception:
            # Do not persist Telegram exception strings: they may include request/token details.
            await self.finish(job,'failed' if job.attempts>=8 else 'pending','delivery_error',min(3600,2**job.attempts))
        return True

    async def _saved(self,uid,pid):
        async with self.sessions() as s: return await s.get(Saved,(uid,pid)) is not None

    async def run(self,sender):
        while True:
            if not await self.process_one(sender): await asyncio.sleep(1)
