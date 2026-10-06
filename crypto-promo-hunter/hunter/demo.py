from datetime import timedelta
from hunter.schema import PromoInput,Exchange,RewardType,utcnow
from hunter.models import User
from hunter.worker import Outbox

def example(exchange):
    return PromoInput(exchange=exchange,source_id=f'demo-{exchange}',
        official_url=f'https://www.{exchange.value}.com/',
        title='DEMO: вымышленная акция Crypto Promo Hunter',
        description='Вымышленный пример. Ссылка ведёт на главную биржи, это не реальная акция.',
        reward='Общий учебный пул 1000 DEMO; индивидуальная сумма неизвестна',
        reward_type=RewardType.POOL,reward_guaranteed=False,
        audience='DEMO: новые пользователи',kyc='DEMO: требуется',countries='Не указано',
        steps=['DEMO: зарегистрироваться в акции','DEMO: внести депозит согласно условиям'],
        deposit='DEMO: 100 условных единиц',volume=None,holding=None,
        start_at=utcnow(),end_at=utcnow()+timedelta(days=7),
        limits='DEMO: ограниченный общий пул, регистрация обязательна',
        raw_text='DEMO — полностью вымышленный пример для проверки; не промоакция биржи.',demo=True)

async def run_demo(service):
    if not service.settings.demo: raise RuntimeError('DEMO_MODE=true required')
    if not service.settings.admin_ids: raise RuntimeError('Set ADMIN_IDS to a local demo identifier, e.g. 1')
    actor=min(service.settings.admin_ids)
    uid=900000001
    await service.user(uid)
    for exchange in Exchange:
        if exchange.value not in await service.subscriptions(uid): await service.toggle_subscription(uid,exchange)
        pid,_=await service.import_manual(actor,example(exchange))
        await service.approve(actor,pid)
        if not await service.is_saved(uid,pid): await service.toggle_saved(uid,pid)
        await service.reminder(uid,pid,60)
    rows,total=await service.list_promos(uid)
    from hunter.render import render_card
    print(f'DEMO initialized: {total} promotions; no Telegram network calls.')
    for p in rows: print(render_card(p))
    outbox=Outbox(service.sessions,service.settings)
    async def forbidden(*args): raise AssertionError('Demo must never contact Telegram')
    while await outbox.process_one(forbidden): pass
