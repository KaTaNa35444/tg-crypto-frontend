from datetime import datetime,timedelta
from sqlalchemy import select, update, or_, func
from hunter.models import User, Subscription, Source, Promotion, Version, Saved, Reminder, Job, aware
from hunter.schema import PromoInput, Exchange, utcnow, material_signature

class DomainError(ValueError): pass

class Service:
    def __init__(self, sessions, settings):
        self.sessions, self.settings = sessions, settings

    def require_admin(self, actor):
        if not self.settings.is_admin(actor): raise PermissionError('Administrator access required')

    async def user(self, uid):
        async with self.sessions.begin() as s:
            user = await s.get(User,uid)
            if not user:
                user = User(id=uid); s.add(user); await s.flush()
            return user

    async def set_user(self, uid, **values):
        await self.user(uid)
        async with self.sessions.begin() as s:
            user = await s.get(User,uid)
            for key,value in values.items(): setattr(user,key,value)

    async def subscriptions(self,uid):
        async with self.sessions() as s:
            return set(await s.scalars(select(Subscription.exchange).where(Subscription.user_id==uid)))

    async def toggle_subscription(self,uid,exchange):
        exchange = Exchange(exchange).value
        await self.user(uid)
        async with self.sessions.begin() as s:
            sub = await s.get(Subscription,(uid,exchange))
            if sub: await s.delete(sub)
            else: s.add(Subscription(user_id=uid,exchange=exchange))

    async def _enqueue(self,s,uid,pid,kind,key,deadline=None):
        # Unique constraint is the final guard. Publication/reminder scheduling locks the parent row.
        if not await s.scalar(select(Job.id).where(Job.key==key)):
            s.add(Job(user_id=uid,promotion_id=pid,kind=kind,key=key,expected_deadline=deadline))

    async def _audit(self,s,p,data,event,actor=None):
        version=Version(promotion_id=p.id,data=data,event=event,actor=actor)
        s.add(version); await s.flush()
        if event in {'discovered','discovered_update','edited'}:
            for uid in self.settings.admin_ids:
                await self._enqueue(s,uid,p.id,'review',f'review:{p.id}:{version.id}:{uid}')

    async def _ingest(self,s,inp,baseline):
        source = await s.get(Source,inp.exchange.value,with_for_update=True)
        if source is None:
            source = Source(exchange=inp.exchange.value,initialized=False)
            s.add(source); await s.flush()
        # Reconcile both identities; collisions need explicit admin intervention.
        found = list(await s.scalars(select(Promotion).where(Promotion.exchange==inp.exchange.value,
                     or_(Promotion.source_id==inp.source_id,Promotion.canonical_url==inp.official_url)).with_for_update()))
        if len(found)>1: raise DomainError('Conflicting source ID and URL: manual reconciliation required')
        payload = inp.payload()
        if found:
            p = found[0]
            p.checked_at = utcnow()
            previous = p.candidate or p.data
            last_source=await s.scalar(select(Version.data).where(Version.promotion_id==p.id,
                Version.event.in_(['discovered','discovered_update'])).order_by(Version.id.desc()).limit(1))
            if previous == payload or last_source == payload: return p,'unchanged'
            # A rejected or closed publication is not automatically resurrected.
            p.candidate = payload
            await self._audit(s,p,payload,'discovered_update')
            return p,'updated'
        p = Promotion(exchange=inp.exchange.value,source_id=inp.source_id,canonical_url=inp.official_url,
                      candidate=payload,baseline=baseline,demo=inp.demo)
        s.add(p); await s.flush()
        await self._audit(s,p,payload,'discovered')
        return p,'new'

    async def import_manual(self,actor,inp: PromoInput):
        self.require_admin(actor)
        if inp.demo != self.settings.demo:
            raise DomainError('Demo and production data must use separate databases')
        async with self.sessions.begin() as s:
            source = await s.get(Source,inp.exchange.value,with_for_update=True)
            baseline = source is None or not source.initialized
            p,result = await self._ingest(s,inp,baseline)
            source = await s.get(Source,inp.exchange.value)
            source.initialized=True; source.last_success=utcnow(); source.last_attempt=utcnow(); source.last_error=None
            await self._audit(s,p,inp.payload(),'manual_import',actor)
            return p.id,result

    async def ingest_batch(self,exchange,items,verified=False):
        if not items: raise DomainError('Empty collection cannot initialize source')
        async with self.sessions.begin() as s:
            source = await s.get(Source,exchange.value,with_for_update=True)
            baseline = source is None or not source.initialized
            ids=[]
            for inp in items:
                if inp.exchange != exchange or inp.demo != self.settings.demo: raise DomainError('Source mismatch')
                p,result=await self._ingest(s,inp,baseline)
                if result!='unchanged':
                    ids.append(p.id)
                    if verified and self.settings.auto_publish and not inp.ambiguity:
                        await self._approve(s,p,None)
            source=await s.get(Source,exchange.value)
            source.initialized=True; source.last_success=utcnow(); source.last_attempt=utcnow(); source.last_error=None
            return ids

    async def source_error(self,exchange,error):
        async with self.sessions.begin() as s:
            source = await s.get(Source,exchange.value)
            if source is None: source=Source(exchange=exchange.value); s.add(source)
            source.last_attempt=utcnow(); source.last_error=error[:500]

    async def _approve(self,s,p,actor):
        if not p.candidate: raise DomainError('No pending revision')
        data=PromoInput.model_validate(p.candidate).payload()
        old=p.data
        if p.status in {'closed','cancelled'}:
            raise DomainError('Closed/cancelled promotions cannot be reopened by approval')
        if p.status=='rejected': p.status='pending'
        p.data=data; p.candidate=None; p.status='published'; p.version+=1
        await self._audit(s,p,data,'approved',actor)
        changed=old is not None and material_signature(old)!=material_signature(data)
        if old is None and not p.baseline:
            recipients=await s.scalars(select(User.id).join(Subscription,User.id==Subscription.user_id).where(
                Subscription.exchange==p.exchange,User.notifications.is_(True),User.blocked.is_(False)))
            for uid in recipients: await self._enqueue(s,uid,p.id,'new',f'new:{p.id}:{uid}')
        elif changed:
            recipients=await s.scalars(select(User.id).join(Saved,User.id==Saved.user_id).where(
                Saved.promotion_id==p.id,User.notifications.is_(True),User.blocked.is_(False)))
            for uid in recipients: await self._enqueue(s,uid,p.id,'update',f'update:{p.id}:{p.version}:{uid}')
        if changed: await self._reschedule(s,p)

    async def approve(self,actor,pid):
        self.require_admin(actor)
        async with self.sessions.begin() as s:
            p=await s.get(Promotion,pid,with_for_update=True)
            if not p: raise DomainError('Promotion not found')
            await self._approve(s,p,actor)

    async def edit(self,actor,pid,inp):
        self.require_admin(actor)
        async with self.sessions.begin() as s:
            p=await s.get(Promotion,pid,with_for_update=True)
            if not p: raise DomainError('Promotion not found')
            if (inp.exchange.value,inp.official_url)!=(p.exchange,p.canonical_url):
                raise DomainError('Editing cannot change exchange or canonical source URL')
            if inp.demo!=p.demo: raise DomainError('Demo flag cannot change')
            p.candidate=inp.payload(); p.checked_at=utcnow()
            await self._audit(s,p,inp.payload(),'edited',actor)

    async def moderate(self,actor,pid,status):
        self.require_admin(actor)
        if status not in {'rejected','closed','cancelled'}: raise DomainError('Invalid status')
        async with self.sessions.begin() as s:
            p=await s.get(Promotion,pid,with_for_update=True)
            if not p: raise DomainError('Promotion not found')
            if status=='rejected' and p.data:
                if not p.candidate: raise DomainError('No pending revision')
                await self._audit(s,p,p.candidate,'rejected_revision',actor)
                p.candidate=None
                return
            p.status=status; p.candidate=None; p.version+=1
            await self._audit(s,p,p.data or {},status,actor)
            await self._reschedule(s,p)
            if status in {'closed','cancelled'}:
                for uid in await s.scalars(select(Saved.user_id).where(Saved.promotion_id==pid)):
                    await self._enqueue(s,uid,p.id,'status',f'status:{pid}:{p.version}:{uid}')

    async def _reschedule(self,s,p):
        await s.execute(update(Job).where(Job.promotion_id==p.id,Job.kind=='reminder',
                       Job.status.in_(['pending','sending'])).values(status='cancelled',lease_token=None))
        for r in await s.scalars(select(Reminder).where(Reminder.promotion_id==p.id)):
            end=(p.data or {}).get('end_at')
            if p.status!='published' or not end:
                r.active=False
            else:
                from datetime import datetime
                r.deadline=end; r.due_at=datetime.fromisoformat(end)-timedelta(minutes=r.minutes)
                r.active=r.due_at>utcnow()

    async def get_promo(self,pid):
        async with self.sessions() as s: return await s.get(Promotion,pid)

    async def list_promos(self,uid,saved=False,page=0):
        exchanges=await self.subscriptions(uid)
        async with self.sessions() as s:
            q=select(Promotion).where(Promotion.data.is_not(None))
            if saved: q=q.join(Saved,Saved.promotion_id==Promotion.id).where(Saved.user_id==uid)
            else: q=q.where(Promotion.status=='published',Promotion.exchange.in_(exchanges))
            rows=list(await s.scalars(q.order_by(Promotion.discovered_at.desc(),Promotion.id.desc())))
        now=utcnow()
        from datetime import datetime
        if not saved: rows=[p for p in rows if not p.data.get('end_at') or datetime.fromisoformat(p.data['end_at'])>now]
        return rows[page:page+1],len(rows)

    async def is_saved(self,uid,pid):
        async with self.sessions() as s: return await s.get(Saved,(uid,pid)) is not None

    async def toggle_saved(self,uid,pid):
        await self.user(uid)
        async with self.sessions.begin() as s:
            p=await s.get(Promotion,pid)
            if not p or p.data is None: raise DomainError('Promotion not available')
            existing=await s.get(Saved,(uid,pid))
            if existing: await s.delete(existing)
            else: s.add(Saved(user_id=uid,promotion_id=pid))

    async def reminder(self,uid,pid,minutes):
        if minutes not in {60,1440}: raise DomainError('Choose 60 or 1440 minutes')
        await self.user(uid)
        from datetime import datetime
        async with self.sessions.begin() as s:
            p=await s.get(Promotion,pid,with_for_update=True)
            if not p or p.status!='published' or p.candidate: raise DomainError('Promotion is not confirmed')
            end=p.data.get('end_at')
            if not end: raise DomainError('Exact deadline with timezone is not known')
            due=datetime.fromisoformat(end)-timedelta(minutes=minutes)
            if due<=utcnow(): raise DomainError('Reminder time has already passed')
            r=await s.get(Reminder,(uid,pid))
            if not r: r=Reminder(user_id=uid,promotion_id=pid); s.add(r)
            r.minutes=minutes; r.due_at=due; r.deadline=end; r.active=True
            await s.execute(update(Job).where(Job.user_id==uid,Job.promotion_id==pid,Job.kind=='reminder',
                            Job.status.in_(['pending','sending'])).values(status='cancelled',lease_token=None))

    async def schedule_reminders(self):
        async with self.sessions.begin() as s:
            rows=await s.scalars(select(Reminder).where(Reminder.active.is_(True),Reminder.due_at<=utcnow()).with_for_update(skip_locked=True))
            for r in rows:
                p=await s.get(Promotion,r.promotion_id)
                if not p or p.status!='published' or p.candidate: continue
                if p.data.get('end_at')!=r.deadline: continue
                if aware(p.checked_at)<utcnow()-timedelta(hours=self.settings.reminder_fresh_hours): continue
                if datetime.fromisoformat(r.deadline)<=utcnow():
                    r.active=False; continue
                await self._enqueue(s,r.user_id,r.promotion_id,'reminder',
                    f'reminder:{r.promotion_id}:{r.user_id}:{r.deadline}:{r.minutes}',r.deadline)
                r.active=False

    async def admin_queue(self,actor):
        self.require_admin(actor)
        async with self.sessions() as s:
            return list(await s.scalars(select(Promotion).where(Promotion.candidate.is_not(None)).order_by(Promotion.id)))

    async def metrics(self,actor):
        self.require_admin(actor)
        async with self.sessions() as s:
            counts={name:await s.scalar(select(func.count()).select_from(model)) for name,model in
                    [('users',User),('subscriptions',Subscription),('saved',Saved),('jobs',Job)]}
            sources=list(await s.scalars(select(Source)))
            return counts,sources

    async def cancel_reminder(self,uid,pid):
        async with self.sessions.begin() as s:
            r=await s.get(Reminder,(uid,pid))
            if r: r.active=False
            await s.execute(update(Job).where(Job.user_id==uid,Job.promotion_id==pid,Job.kind=='reminder',
                Job.status.in_(['pending','sending'])).values(status='cancelled',lease_token=None))

    async def import_manual_batch(self,actor,items):
        self.require_admin(actor)
        if not items or len(items)>50: raise DomainError('Import 1 to 50 promotions')
        if len({p.exchange for p in items})!=1: raise DomainError('One exchange per batch')
        if any(p.demo!=self.settings.demo for p in items): raise DomainError('Demo/production mismatch')
        async with self.sessions.begin() as s:
            exchange=items[0].exchange.value
            source=await s.get(Source,exchange,with_for_update=True)
            baseline=source is None or not source.initialized
            results=[]
            for inp in items:
                p,result=await self._ingest(s,inp,baseline)
                await self._audit(s,p,inp.payload(),'manual_import',actor)
                results.append((p.id,result))
            source=await s.get(Source,exchange)
            source.initialized=True; source.last_success=utcnow(); source.last_attempt=utcnow(); source.last_error=None
            return results
