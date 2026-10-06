from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

WORDS = {
 'ru': {'unknown':'Не указано','menu':['Новые акции','Мои биржи','Сохранённые','Настройки уведомлений','О боте'],
        'source':'Официальный источник','checked':'Последняя проверка','reward':'Награда','audience':'Кому доступна',
        'countries':'Ограничения по странам','steps':'Участие','deposit':'Требуемый депозит','volume':'Торговый объём',
        'holding':'Срок удержания средств','start':'Начало','end':'Окончание','limits':'Ограничения',
        'crediting':'Начисление','usage':'Использование','withdrawal':'Вывод награды','save':'Сохранить','remove':'Удалить',
        'remind':'Напомнить','official':'Официальная акция','no':'Нет акций. Выберите биржи или дождитесь публикации.',
        'back':'Меню','timezone':'Часовой пояс','notify':'Рассылка','on':'Включена','off':'Отключена',
        'reminder':'Напоминание','update':'Условия изменились','status':'Статус изменился','new':'Новая акция',
        'types':{'withdrawable':'выводимые средства','trading_bonus':'торговый бонус (не выводимые деньги)',
                 'voucher':'ваучер','draw':'розыгрыш (не гарантированная награда)','pool':'общий пул (не награда каждому)',
                 'unknown':'тип не указан'},
        'guarantee':'Гарантия награды','not_guaranteed':'Не гарантирована; максимум не означает гарантированную выплату',
        'hour':'За 1 час','day':'За 24 часа','about':'Отслеживаем официальные промоакции. Бот не торгует и не подключается к аккаунтам бирж. Не присылайте seed-фразы, приватные ключи или API-ключи. Проверяйте условия на официальном сайте. Данные могут быть неполными.'},
 'ua': {'unknown':'Не вказано','menu':['Нові акції','Мої біржі','Збережені','Налаштування сповіщень','Про бота'],
        'source':'Офіційне джерело','checked':'Остання перевірка','reward':'Винагорода','audience':'Кому доступна',
        'countries':'Обмеження за країнами','steps':'Участь','deposit':'Потрібний депозит','volume':'Торговий обсяг',
        'holding':'Строк утримання коштів','start':'Початок','end':'Закінчення','limits':'Обмеження',
        'crediting':'Нарахування','usage':'Використання','withdrawal':'Виведення винагороди','save':'Зберегти','remove':'Видалити',
        'remind':'Нагадати','official':'Офіційна акція','no':'Немає акцій. Виберіть біржі або дочекайтеся публікації.',
        'back':'Меню','timezone':'Часовий пояс','notify':'Розсилка','on':'Увімкнена','off':'Вимкнена',
        'reminder':'Нагадування','update':'Умови змінилися','status':'Статус змінився','new':'Нова акція',
        'types':{'withdrawable':'кошти для виведення','trading_bonus':'торговий бонус (не гроші для виведення)',
                 'voucher':'ваучер','draw':'розіграш (винагорода не гарантована)','pool':'загальний пул (не винагорода кожному)',
                 'unknown':'тип не вказано'},
        'guarantee':'Гарантія винагороди','not_guaranteed':'Не гарантована; максимум не означає гарантовану виплату',
        'hour':'За 1 годину','day':'За 24 години','about':'Відстежуємо офіційні промоакції. Бот не торгує та не підключається до акаунтів бірж. Не надсилайте seed-фрази, приватні ключі або API-ключі. Перевіряйте умови на офіційному сайті. Дані можуть бути неповними.'},
}

def words(language): return WORDS.get(language,WORDS['ru'])

def date_text(data,key,tz,unknown):
    dt=data.get(key+'_at')
    if dt: return datetime.fromisoformat(dt).astimezone(ZoneInfo(tz)).strftime('%d.%m.%Y %H:%M %Z')+f' ({tz})'
    raw=data.get(key+'_text')
    if raw: return raw+' [TZ: '+(data.get('source_timezone') or unknown)+']'
    return unknown

def render_card(p,language='ru',tz='Europe/Kyiv',candidate=False):
    w=words(language); d=(p.candidate if candidate else p.data) or {}; e=escape
    unknown=w['unknown']
    def val(key): return e(str(d.get(key) or unknown))
    lines=[]
    if d.get('demo'): lines.append('<b>DEMO — ВЫМЫШЛЕННАЯ АКЦИЯ / ВИГАДАНА АКЦІЯ</b>')
    lines.extend([f'<b>{e(p.exchange.upper())} · {val("title")}</b>',val('description'),
                  f'{w["reward"]}: {val("reward")} — {e(w["types"].get(d.get("reward_type"),unknown))}',
                  f'{w["guarantee"]}: '+(w['not_guaranteed'] if d.get('reward_guaranteed') is False else unknown
                       if d.get('reward_guaranteed') is None else ('Да, по условиям источника' if language=='ru' else 'Так, за умовами джерела'))])
    for key in ['audience','kyc','countries']: lines.append(f'{w.get(key,"KYC")}: {val(key)}')
    lines.append(w['steps']+':')
    lines.extend([f'{i+1}. {e(step)}' for i,step in enumerate(d.get('steps',[]))] or [unknown])
    for key in ['deposit','volume','holding']: lines.append(f'{w[key]}: {val(key)}')
    for key in ['start','end']: lines.append(f'{w[key]}: {e(date_text(d,key,tz,unknown))}')
    for key in ['limits','crediting','usage','withdrawal']: lines.append(f'{w[key]}: {val(key)}')
    lines.append(f'<a href="{e(d.get("official_url",p.canonical_url),quote=True)}">{w["source"]}</a>')
    from hunter.models import aware
    lines.append(w['checked']+': '+aware(p.checked_at).astimezone(ZoneInfo(tz)).strftime('%d.%m.%Y %H:%M')+f' ({e(tz)})')
    lines.append('Status: '+e(p.status))
    if candidate:
        lines.append('Review:')
        lines.extend(e(note) for note in (d.get('ambiguity') or ['Compare all fields with original source']))
    return '\n'.join(lines)


def chunks(text,limit=3800):
    """Split HTML only at complete lines, never inside a tag/entity."""
    result=[]; current=''
    for line in text.splitlines():
        if len(line)>limit: raise ValueError('Card line too long')
        if len(current)+len(line)+1>limit:
            result.append(current); current=''
        current+=('\n' if current else '')+line
    if current: result.append(current)
    return result
