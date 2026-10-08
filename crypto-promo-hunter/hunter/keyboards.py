from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton as B
from hunter.render import words

def keyboard(rows): return InlineKeyboardMarkup(inline_keyboard=rows)
def button(text,data): return B(text=text,callback_data=data)
def menu(language):
    return keyboard([[button(text,data)] for text,data in zip(words(language)['menu'],
        ['list:new:0','exchanges','list:saved:0','settings','about'])])
def card_buttons(p,language,saved=False):
    w=words(language)
    return [[B(text=w['official'],url=p.data['official_url'])],
            [button(w['remove'] if saved else w['save'],f'save:{p.id}'),button(w['remind'],f'remind:{p.id}')]]
