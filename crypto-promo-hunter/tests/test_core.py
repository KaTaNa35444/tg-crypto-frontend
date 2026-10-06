from datetime import timedelta
from pathlib import Path
from dataclasses import replace
import pytest
from sqlalchemy import select,update
from pydantic import ValidationError
from hunter.schema import PromoInput,Exchange,RewardType,utcnow,canonical_url
from hunter.adapters import ADAPTERS,SourceUnavailable
from hunter.models import Promotion,Job,Reminder,Version,User
from hunter.service import DomainError
from hunter.worker import Outbox
from hunter.render import render_card


def promo(source_id='a',exchange=Exchange.BYBIT,**changes):
    data=dict(exchange=exchange,source_id=source_id,official_url=f'https://www.{exchange.value}.com/{source_id}',
        title='Example <script>not executable</script>',reward='Shared 1000-unit pool',reward_type=RewardType.POOL,
        reward_guaranteed=False,raw_text='Source evidence',end_at=utcnow()+timedelta(days=5))
    data.update(changes)
    return PromoInput(**data)

async def publish(service,inp):
    pid,_=await service.import_manual(1,inp)
    await service.approve(1,pid)
    return pid

async def jobs(service,kind):
    async with service.sessions() as s: return list(await s.scalars(select(Job).where(Job.kind==kind)))

@pytest.mark.parametrize('exchange',list(Exchange))
def test_snapshot_parsing(exchange):
    adapter=ADAPTERS[exchange]()
    html=(Path(__file__).parent/'fixtures'/f'{exchange}.html').read_text()
    inp=adapter.parse_snapshot(html,f'https://www.{exchange}.com/')
    assert inp.raw_text and inp.title.startswith('Example')
    assert inp.reward_type in {RewardType.POOL,RewardType.BONUS,RewardType.DRAW}
    assert inp.ambiguity and not adapter.verified
    if exchange==Exchange.BYBIT:
        assert inp.deposit=='Deposit 50 units' and len(inp.steps)==2
        assert inp.end_at is None and inp.end_text and inp.source_timezone is None

@pytest.mark.parametrize('url',[
    'http://www.bybit.com/x','https://www.bybit.com.evil.test/x','https://evil.test/x',
    'https://www.bybit.com@evil.test/x','https://user@www.bybit.com/x',
    'https://www.bybit.com:444/x','https://www.bybit.com/\\evil'])
def test_url_rejection(url):
    with pytest.raises(ValueError): canonical_url(url,Exchange.BYBIT)

def test_canonical_url():
    assert canonical_url('https://www.bybit.com/x?utm_source=abc&b=2&a=1#top',Exchange.BYBIT)=='https://www.bybit.com/x?a=1&b=2'

def test_missing_and_ambiguous_dates():
    p=promo(end_at=None,end_text='12 October 18:00')
    assert p.end_at is None and p.end_text and p.ambiguity
    with pytest.raises(ValidationError): promo(end_at='2026-10-12T18:00:00')
    with pytest.raises(ValidationError): promo(reward_guaranteed=True)
    with pytest.raises(ValidationError): promo(unexpected='prompt injection')

async def test_unconfigured_sources_are_not_mock_integrations():
    for cls in ADAPTERS.values():
        adapter=cls(); adapter.url=''; adapter.selector=''
        with pytest.raises(SourceUnavailable): await adapter.collect()

async def test_dedup_and_evidence_history(service):
    inp=promo()
    pid,result=await service.import_manual(1,inp)
    assert result=='new'
    pid2,result=await service.import_manual(1,inp)
    assert pid==pid2 and result=='unchanged'
    alias=inp.model_copy(update={'source_id':'different-id'})
    assert (await service.import_manual(1,alias))[0]==pid
    async with service.sessions() as s:
        assert len(list(await s.scalars(select(Promotion))))==1
        versions=list(await s.scalars(select(Version)))
        assert versions and versions[0].data['raw_text']=='Source evidence'

async def test_baseline_subscription_filter_and_opt_out(service):
    await service.toggle_subscription(10,Exchange.BYBIT)
    await service.toggle_subscription(20,Exchange.BITGET)
    await service.toggle_subscription(30,Exchange.BYBIT)
    await service.set_user(30,notifications=False)
    await publish(service,promo('baseline'))
    assert not await jobs(service,'new')
    pid=await publish(service,promo('new'))
    assert [(j.user_id,j.promotion_id) for j in await jobs(service,'new')]==[(10,pid)]
    await service.import_manual(1,promo('new',end_at=(await service.get_promo(pid)).data['end_at']))
    assert len(await jobs(service,'new'))==1
    _,total=await service.list_promos(20)
    assert total==0

async def test_material_vs_cosmetic_update_and_approval(service):
    pid=await publish(service,promo())
    await service.user(10); await service.toggle_saved(10,pid)
    p=await service.get_promo(pid)
    inp=PromoInput.model_validate(p.data)
    updated=inp.model_copy(update={'reward':'Shared 2000-unit pool'})
    await service.import_manual(1,updated)
    assert not await jobs(service,'update')  # Approval is mandatory.
    await service.approve(1,pid)
    assert len(await jobs(service,'update'))==1
    cosmetic=updated.model_copy(update={'title':'A new title','raw_text':'Whitespace and typography update'})
    await service.import_manual(1,cosmetic); await service.approve(1,pid)
    assert len(await jobs(service,'update'))==1
    assert (await service.get_promo(pid)).data['reward']=='Shared 2000-unit pool'

async def test_batch_initialization_is_silent_and_empty_does_not_initialize(service):
    with pytest.raises(DomainError): await service.ingest_batch(Exchange.BYBIT,[])
    await service.toggle_subscription(10,Exchange.BYBIT)
    ids=await service.ingest_batch(Exchange.BYBIT,[promo('a'),promo('b')])
    for pid in ids: await service.approve(1,pid)
    assert not await jobs(service,'new')
    ids=await service.ingest_batch(Exchange.BYBIT,[promo('c')])
    await service.approve(1,ids[0]); assert len(await jobs(service,'new'))==1

async def test_reminder_move_cancel_and_unknown_date(service):
    pid=await publish(service,promo())
    await service.reminder(10,pid,60)
    p=await service.get_promo(pid)
    new=PromoInput.model_validate(p.data).model_copy(update={'end_at':utcnow()+timedelta(days=7)})
    await service.import_manual(1,new); await service.approve(1,pid)
    async with service.sessions() as s:
        r=await s.get(Reminder,(10,pid))
        assert r.deadline==new.payload()['end_at'] and r.active
    await service.moderate(1,pid,'cancelled')
    async with service.sessions() as s: assert not (await s.get(Reminder,(10,pid))).active
    pid2=await publish(service,promo('unknown',end_at=None,end_text='October 15'))
    with pytest.raises(DomainError): await service.reminder(10,pid2,60)

async def test_reminder_queue_rechecks_and_is_idempotent(service):
    pid=await publish(service,promo(end_at=utcnow()+timedelta(hours=2)))
    await service.reminder(10,pid,60)
    async with service.sessions.begin() as s:
        r=await s.get(Reminder,(10,pid)); r.due_at=utcnow()-timedelta(minutes=1)
    await service.schedule_reminders(); await service.schedule_reminders()
    pending=await jobs(service,'reminder'); assert len(pending)==1
    await service.moderate(1,pid,'cancelled')
    assert await Outbox(service.sessions,service.settings).deliverable(pending[0]) is None
    assert (await jobs(service,'reminder'))[0].status=='cancelled'

async def test_stale_unverified_reminders_held(service):
    pid=await publish(service,promo(end_at=utcnow()+timedelta(hours=2)))
    await service.reminder(10,pid,60)
    async with service.sessions.begin() as s:
        (await s.get(Reminder,(10,pid))).due_at=utcnow()-timedelta(minutes=1)
        (await s.get(Promotion,pid)).checked_at=utcnow()-timedelta(days=2)
    await service.schedule_reminders(); assert not await jobs(service,'reminder')

async def test_admin_authorization_at_service_boundary(service):
    for action in [lambda:service.import_manual(99,promo()),lambda:service.approve(99,1),
        lambda:service.moderate(99,1,'cancelled'),lambda:service.metrics(99),lambda:service.admin_queue(99),
        lambda:service.edit(99,1,promo())]:
        with pytest.raises(PermissionError): await action()

async def test_claim_recovery_and_no_normal_repeat(service):
    await publish(service,promo())
    queue=Outbox(service.sessions,service.settings)
    job=await queue.claim(); assert job
    assert await queue.claim() is None
    async with service.sessions.begin() as s:
        (await s.get(Job,job.id)).lease_until=utcnow()-timedelta(seconds=1)
    recovered=await Outbox(service.sessions,service.settings).claim()
    assert recovered.id==job.id and recovered.lease_token!=job.lease_token
    await queue.finish(job,'sent')  # Expired worker cannot acknowledge another worker's lease.
    await queue.finish(recovered,'sent')
    assert await queue.claim() is None

async def test_opt_out_rechecked_before_dispatch(service):
    await service.toggle_subscription(10,Exchange.BYBIT)
    await publish(service,promo('baseline')); await publish(service,promo('fresh'))
    job=(await jobs(service,'new'))[0]
    await service.set_user(10,notifications=False)
    assert await Outbox(service.sessions,service.settings).deliverable(job) is None

async def test_card_accuracy_and_escaping(service):
    pid=await publish(service,promo(end_at=None,end_text='12 October 18:00'))
    card=render_card(await service.get_promo(pid))
    assert '&lt;script&gt;' in card and '<script>' not in card
    assert 'общий пул (не награда каждому)' in card
    assert 'Не указано' in card and 'TZ: Не указано' in card
    assert 'Требуемый депозит: Не указано' in card
    ua=render_card(await service.get_promo(pid),'ua','Europe/Kyiv')
    assert 'загальний пул' in ua and 'Не вказано' in ua

async def test_demo_never_calls_telegram(service,capsys):
    from hunter.demo import run_demo
    service.settings=replace(service.settings,demo=True,bot_token='not-a-real-token')
    await run_demo(service)
    assert 'no Telegram network calls' in capsys.readouterr().out

async def test_initial_manual_batch_is_silent(service):
    await service.toggle_subscription(10,Exchange.BYBIT)
    results=await service.import_manual_batch(1,[promo('old-a'),promo('old-b')])
    for pid,_ in results: await service.approve(1,pid)
    assert not await jobs(service,'new')

async def test_telegram_rate_limit_and_user_block(service):
    from aiogram.exceptions import TelegramRetryAfter,TelegramForbiddenError
    from aiogram.methods import SendMessage
    from hunter.models import aware
    pid=await publish(service,promo('old'))
    await service.toggle_subscription(10,Exchange.BYBIT)
    pid=await publish(service,promo('new'))
    async with service.sessions.begin() as s:
        await s.execute(update(Job).where(Job.kind=='review').values(status='sent'))
    queue=Outbox(service.sessions,service.settings)
    async def limited(*args,**kwargs): raise TelegramRetryAfter(SendMessage(chat_id=10,text='x'),'limit',retry_after=5)
    await queue.process_one(limited)
    job=(await jobs(service,'new'))[0]
    assert job.status=='pending' and aware(job.available_at)>utcnow()
    async with service.sessions.begin() as s:
        (await s.get(Job,job.id)).available_at=utcnow()-timedelta(seconds=1)
    async def blocked(*args,**kwargs): raise TelegramForbiddenError(SendMessage(chat_id=10,text='x'),'blocked')
    await queue.process_one(blocked)
    assert (await jobs(service,'new'))[0].status=='cancelled'
    assert (await service.user(10)).blocked

async def test_job_sends_once_during_normal_processing(service):
    await publish(service,promo('old')); await service.toggle_subscription(10,Exchange.BYBIT)
    await publish(service,promo('fresh'))
    async with service.sessions.begin() as s:
        await s.execute(update(Job).where(Job.kind=='review').values(status='sent'))
    delivered=[]
    async def sender(uid,text,**kwargs): delivered.append((uid,text,kwargs))
    queue=Outbox(service.sessions,service.settings)
    while await queue.process_one(sender): pass
    assert len(delivered)==1 and delivered[0][0]==10 and delivered[0][2]['reply_markup']
    assert not await queue.process_one(sender)

async def test_pending_revision_rejection_preserves_approved_card(service):
    pid=await publish(service,promo())
    original=(await service.get_promo(pid)).data
    await service.edit(1,pid,PromoInput.model_validate(original).model_copy(update={'reward':'Unverified claim'}))
    await service.moderate(1,pid,'rejected')
    p=await service.get_promo(pid)
    assert p.status=='published' and p.data==original and p.candidate is None

async def test_unverified_adapter_cannot_auto_publish(service):
    service.settings=replace(service.settings,auto_publish=True)
    ids=await service.ingest_batch(Exchange.BYBIT,[promo()],verified=False)
    assert (await service.get_promo(ids[0])).status=='pending'

async def test_postgres_workers_cannot_claim_same_job(service):
    import asyncio
    if not service.settings.database_url.startswith('postgresql'):
        pytest.skip('Row locking requires PostgreSQL; exercised in disposable PostgreSQL run')
    await publish(service,promo())
    a,b=await asyncio.gather(Outbox(service.sessions,service.settings).claim(),
                             Outbox(service.sessions,service.settings).claim())
    assert (a is None)!=(b is None)

def test_logs_redact_tokens_database_url_and_exception_payload():
    import logging
    from hunter.logging_safe import Redact
    token='123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk'
    url='postgresql+asyncpg://user:dummy-password@db/database'
    record=logging.LogRecord('test',logging.ERROR,'',1,'request %s %s',(token,url),None)
    assert Redact([token,url]).filter(record)
    assert token not in record.getMessage() and 'dummy-password' not in record.getMessage()

async def test_unchanged_source_does_not_overwrite_admin_corrections(service):
    original=promo()
    pid,_=await service.import_manual(1,original)
    corrected=original.model_copy(update={'kyc':'Confirmed KYC requirement'})
    await service.edit(1,pid,corrected)
    assert (await service.import_manual(1,original))[1]=='unchanged'
    assert (await service.get_promo(pid)).candidate['kyc']=='Confirmed KYC requirement'

def test_maximum_reward_and_trading_bonus_are_not_cash_guarantees():
    with pytest.raises(ValidationError):
        promo(reward='Up to 500 USDT',reward_type=RewardType.CASH,reward_guaranteed=True)
    with pytest.raises(ValidationError):
        promo(reward='До 100 USDT',reward_type=RewardType.CASH,reward_guaranteed=True)
    with pytest.raises(ValidationError):
        promo(reward='Trading bonus 100 USDT',reward_type=RewardType.CASH)

async def test_source_retry_after_is_respected_without_bypassing(monkeypatch):
    import httpx,time
    calls=[]
    class Client:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,**kwargs):
            calls.append(url)
            return httpx.Response(429,headers={'Retry-After':'120'},request=httpx.Request('GET',url))
    monkeypatch.setattr('hunter.adapters.httpx.AsyncClient',Client)
    adapter=ADAPTERS[Exchange.BYBIT]()
    with pytest.raises(SourceUnavailable): await adapter.fetch('https://www.bybit.com/')
    assert adapter._next_allowed>time.monotonic()+100
    with pytest.raises(SourceUnavailable): await adapter.fetch('https://www.bybit.com/')
    assert len(calls)==1
