from datetime import datetime,timezone
from aiogram import Bot,Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage,EditMessageReplyMarkup,AnswerCallbackQuery,GetMe
from aiogram.types import Message,Chat,User,Update,CallbackQuery
from hunter.bot import router
from test_core import promo,publish

class FakeTelegram(BaseSession):
    def __init__(self): super().__init__(); self.calls=[]
    async def close(self): pass
    async def make_request(self,bot,method,timeout=None):
        self.calls.append(method)
        if isinstance(method,GetMe): return User(id=123,is_bot=True,first_name='Hunter',username='HunterTestBot')
        if isinstance(method,AnswerCallbackQuery): return True
        return Message(message_id=100,date=datetime.now(timezone.utc),chat=Chat(id=method.chat_id,type='private'),
            text=getattr(method,'text',''),reply_markup=getattr(method,'reply_markup',None))
    async def stream_content(self,*args,**kwargs):
        if False: yield b''

async def test_user_journey_and_admin_callback_denial(service):
    session=FakeTelegram()
    bot=Bot('123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk',session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp=Dispatcher(); dp.include_router(router(service))
    number=0
    def message(text,uid=10):
        return Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=uid,type='private'),
            from_user=User(id=uid,is_bot=False,first_name='Test'),text=text)
    async def command(text,uid=10):
        nonlocal number
        number+=1; await dp.feed_update(bot,Update(update_id=number,message=message(text,uid)))
    async def click(data,uid=10):
        nonlocal number
        number+=1
        await dp.feed_update(bot,Update(update_id=number,callback_query=CallbackQuery(id=str(number),
            from_user=User(id=uid,is_bot=False,first_name='Test'),chat_instance='1',data=data,message=message('card',uid))))
    await command('/start')
    menu=session.calls[-1].reply_markup
    assert [row[0].text for row in menu.inline_keyboard]==['Новые акции','Мои биржи','Сохранённые','Настройки уведомлений','О боте']
    await click('exchange:bybit'); assert await service.subscriptions(10)=={'bybit'}
    pid=await publish(service,promo())
    await click('list:new:0')
    assert any(isinstance(c,SendMessage) and 'общий пул' in c.text for c in session.calls)
    await click(f'save:{pid}'); assert await service.is_saved(10,pid)
    await click(f'reminder:{pid}:60')
    await click('language:ua'); assert (await service.user(10)).language=='ua'
    await command('/timezone America/New_York'); assert (await service.user(10)).timezone=='America/New_York'
    await click('notify'); assert not (await service.user(10)).notifications
    await click(f'admin:reject:{pid}')
    assert (await service.get_promo(pid)).status=='published'
    assert isinstance(session.calls[-1],AnswerCallbackQuery) and session.calls[-1].show_alert
    await command('/admin')
    assert 'Доступ запрещён' in session.calls[-1].text
    await bot.session.close()
