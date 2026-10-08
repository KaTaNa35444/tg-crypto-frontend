import io
from html import escape
import json
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from aiogram import Router,F
from aiogram.filters import Command,CommandStart
from aiogram.types import Message,CallbackQuery
from aiogram import BaseMiddleware
from hunter.schema import PromoInput,Exchange
from hunter.service import DomainError
from hunter.render import render_card,chunks,words
from hunter.keyboards import keyboard,button,menu,card_buttons

class Errors(BaseMiddleware):
    async def __call__(self,handler,event,data):
        if getattr(event,'chat',None) and event.chat.type!='private': return
        if isinstance(event,CallbackQuery) and (not event.message or event.message.chat.type!='private'): return
        try: return await handler(event,data)
        except PermissionError:
            if isinstance(event,CallbackQuery): await event.answer('Доступ запрещён / Доступ заборонено',show_alert=True)
            else: await event.answer('Доступ запрещён / Доступ заборонено')
        except (DomainError,ValueError,KeyError):
            text='Проверьте поля, ID и срок акции. / Перевірте поля, ID та строк акції.'
            if isinstance(event,CallbackQuery): await event.answer(text,show_alert=True)
            else: await event.answer(text)
        except Exception:
            # No exception bodies or Telegram objects go to logs.
            if isinstance(event,CallbackQuery): await event.answer('Ошибка; повторите позже / Помилка; повторіть пізніше',show_alert=True)
            else: await event.answer('Ошибка; повторите позже / Помилка; повторіть пізніше')


def router(service):
    r=Router(); r.message.middleware(Errors()); r.callback_query.middleware(Errors())

    async def send_card(message,p,u,rows=None,candidate=False):
        text=render_card(p,u.language,u.timezone,candidate)
        parts=chunks(text)
        if rows is None: rows=card_buttons(p,u.language,await service.is_saved(u.id,p.id))
        for i,part in enumerate(parts):
            await message.answer(part,reply_markup=keyboard(rows) if i==len(parts)-1 else None)

    @r.message(CommandStart())
    async def start(m: Message):
        await service.set_user(m.from_user.id,blocked=False)
        u=await service.user(m.from_user.id)
        await m.answer('Crypto Promo Hunter',reply_markup=menu(u.language))

    @r.message(Command('timezone'))
    async def timezone(m: Message):
        parts=m.text.split(maxsplit=1)
        if len(parts)!=2: return await m.answer('/timezone Europe/Kyiv')
        tz=parts[1].strip()
        if len(tz)>80: raise DomainError('Invalid timezone')
        try: ZoneInfo(tz)
        except ZoneInfoNotFoundError: raise DomainError('Unknown timezone')
        await service.set_user(m.from_user.id,timezone=tz)
        await m.answer('✓ '+tz)

    @r.message(Command('admin'))
    async def admin(m: Message):
        queue=await service.admin_queue(m.from_user.id)
        await m.answer(f'На проверке / На перевірці: {len(queue)}\n'
            '/review ID · /approve ID · /reject ID · /close ID · /cancel ID\n'
            '/import JSON · /edit ID JSON · /stats\n'
            'JSON можно отправить документом с подписью /import или /edit ID.\n'
            'Полный исходный текст: /evidence ID')
        # Avoid flooding Telegram with all pending cards; review any ID explicitly.
        for p in queue[:10]: await m.answer(f'#{p.id} {p.exchange.upper()}: '+escape(p.candidate['title']))
        if len(queue)>10: await m.answer('Полный список: /queue OFFSET')

    @r.message(Command('queue'))
    async def queue(m: Message):
        rows=await service.admin_queue(m.from_user.id)
        offset=int(m.text.split()[1]) if len(m.text.split())>1 else 0
        if offset<0: raise DomainError('Invalid offset')
        for p in rows[offset:offset+10]: await m.answer(f'#{p.id} '+escape(p.candidate['title']))

    @r.message(Command('review','evidence'))
    async def review(m: Message):
        service.require_admin(m.from_user.id)
        pid=int(m.text.split()[1]); p=await service.get_promo(pid)
        if not p: raise DomainError('Not found')
        if m.text.split()[0].split('@')[0]=='/evidence':
            from aiogram.types import BufferedInputFile
            content=json.dumps(p.candidate or p.data,ensure_ascii=False,indent=2).encode()
            await m.answer_document(BufferedInputFile(content,filename=f'promotion-{pid}.json'))
        else:
            u=await service.user(m.from_user.id)
            rows=[[button('Publish / Опубликовать',f'admin:approve:{pid}'),button('Reject / Отклонить',f'admin:reject:{pid}')]]
            await send_card(m,p,u,rows,candidate=bool(p.candidate))

    @r.message(Command('approve','reject','close','cancel'))
    async def moderate(m: Message):
        service.require_admin(m.from_user.id)
        command=m.text.split()[0].split('@')[0][1:]; pid=int(m.text.split()[1])
        if command=='approve': await service.approve(m.from_user.id,pid)
        else: await service.moderate(m.from_user.id,pid,{'reject':'rejected','close':'closed','cancel':'cancelled'}[command])
        await m.answer('✓')

    async def import_data(m,command,raw):
        service.require_admin(m.from_user.id)
        parsed=json.loads(raw)
        if isinstance(parsed,list):
            if command.startswith('/edit'): raise DomainError('Editing expects one object')
            results=await service.import_manual_batch(m.from_user.id,[PromoInput.model_validate(item) for item in parsed])
            await m.answer('Imported for review: '+', '.join(str(pid) for pid,event in results))
            return
        inp=PromoInput.model_validate(parsed)
        if command.startswith('/edit'):
            pid=int(command.split()[1]); await service.edit(m.from_user.id,pid,inp)
            await m.answer(f'#{pid}: revision pending approval / требуется одобрение')
        else:
            pid,event=await service.import_manual(m.from_user.id,inp)
            await m.answer(f'#{pid}: {event}; /review {pid}')

    @r.message(Command('import','edit'))
    async def manual(m: Message):
        service.require_admin(m.from_user.id)
        parts=m.text.split(maxsplit=2 if m.text.startswith('/edit') else 1)
        if m.text.startswith('/edit'):
            if len(parts)<3: raise DomainError('Use /edit ID JSON')
            await import_data(m,' '.join(parts[:2]),parts[2])
        else:
            if len(parts)<2: raise DomainError('Use /import JSON')
            await import_data(m,parts[0],parts[1])

    @r.message(F.document)
    async def document(m: Message):
        service.require_admin(m.from_user.id)
        if not m.caption or m.caption.split()[0].split('@')[0] not in {'/import','/edit'}:
            raise DomainError('Caption /import or /edit ID required')
        if not m.document.file_size or m.document.file_size>150000: raise DomainError('File exceeds limit')
        buffer=io.BytesIO(); await m.bot.download(m.document,destination=buffer)
        await import_data(m,m.caption,buffer.getvalue().decode('utf-8'))

    @r.message(Command('stats'))
    async def stats(m: Message):
        counts,sources=await service.metrics(m.from_user.id)
        from html import escape
        await m.answer(escape(json.dumps(counts,ensure_ascii=False)))
        for src in sources:
            await m.answer(escape(f'{src.exchange}: initialized={src.initialized}; last success={src.last_success}; '
                                 f'last attempt={src.last_attempt}; error={src.last_error or "none"}'))

    @r.callback_query()
    async def callback(c: CallbackQuery):
        u=await service.user(c.from_user.id); w=words(u.language); data=c.data or ''
        if data=='admin_queue':
            queue=await service.admin_queue(u.id)
            await c.message.answer(' · '.join(f'/review {p.id}' for p in queue[:10]) or 'Очередь пуста / Черга порожня')
        elif data=='menu': await c.message.answer('Crypto Promo Hunter',reply_markup=menu(u.language))
        elif data=='about': await c.message.answer(w['about'],reply_markup=keyboard([[button(w['back'],'menu')]]))
        elif data=='exchanges' or data.startswith('exchange:'):
            if data.startswith('exchange:'): await service.toggle_subscription(u.id,data.split(':')[1])
            selected=await service.subscriptions(u.id)
            await c.message.answer(w['menu'][1],reply_markup=keyboard(
                [[button(('✓ ' if ex.value in selected else '○ ')+ex.value.upper(),f'exchange:{ex.value}')] for ex in Exchange]
                +[[button(w['back'],'menu')]]))
        elif data.startswith('list:'):
            _,kind,page=data.split(':'); page=int(page)
            if kind not in {'new','saved'} or page<0: raise DomainError('Invalid page')
            rows,total=await service.list_promos(u.id,kind=='saved',page)
            if not rows: await c.message.answer(w['no'],reply_markup=menu(u.language))
            else:
                p=rows[0]; actions=card_buttons(p,u.language,await service.is_saved(u.id,p.id))
                nav=[]
                if page>0: nav.append(button('←',f'list:{kind}:{page-1}'))
                if page+1<total: nav.append(button('→',f'list:{kind}:{page+1}'))
                if nav: actions.append(nav)
                actions.append([button(f'{page+1}/{total} · '+w['back'],'menu')])
                await send_card(c.message,p,u,actions)
        elif data.startswith('save:'):
            pid=int(data.split(':')[1]); await service.toggle_saved(u.id,pid)
            p=await service.get_promo(pid)
            await c.message.edit_reply_markup(reply_markup=keyboard(card_buttons(p,u.language,await service.is_saved(u.id,pid))+[[button(w['back'],'menu')]]))
        elif data.startswith('remind:'):
            pid=int(data.split(':')[1]); await c.message.answer(w['remind'],reply_markup=keyboard([
                [button(w['hour'],f'reminder:{pid}:60'),button(w['day'],f'reminder:{pid}:1440')],
                [button('✕',f'unremind:{pid}')]]))
        elif data.startswith('reminder:'):
            _,pid,minutes=data.split(':'); await service.reminder(u.id,int(pid),int(minutes)); await c.message.answer('✓')
        elif data.startswith('unremind:'):
            await service.cancel_reminder(u.id,int(data.split(':')[1])); await c.message.answer('✓')
        elif data=='settings' or data.startswith('language:') or data=='notify':
            if data.startswith('language:'):
                lang=data.split(':')[1]
                if lang not in {'ru','ua'}: raise DomainError('Invalid language')
                await service.set_user(u.id,language=lang)
            if data=='notify': await service.set_user(u.id,notifications=not u.notifications)
            u=await service.user(u.id); w=words(u.language)
            await c.message.answer(w['timezone']+': '+u.timezone+'\n/timezone Europe/Kyiv',reply_markup=keyboard([
                [button('RU','language:ru'),button('UA','language:ua')],
                [button(w['notify']+': '+w['on' if u.notifications else 'off'],'notify')],
                [button(w['back'],'menu')]]))
        elif data.startswith('admin:'):
            service.require_admin(u.id)
            _,action,pid=data.split(':')
            if action=='approve': await service.approve(u.id,int(pid))
            elif action=='reject': await service.moderate(u.id,int(pid),'rejected')
            else: raise DomainError('Invalid action')
            await c.message.answer('✓')
        await c.answer()
    return r
