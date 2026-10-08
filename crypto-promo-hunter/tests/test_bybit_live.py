import json
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import pytest
from hunter.adapters import BybitAdapter,SourceUnavailable
from hunter.schema import RewardType

FIX=Path(__file__).parent/'fixtures'

def fixture(name): return (FIX/name).read_text()

def page(data): return '<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':data}})+'</script>'

def listing(): return BybitAdapter.page_data(fixture('bybit-live-listing.html'))


def test_real_article_preserves_conditions_and_deadline_ambiguity():
    p=BybitAdapter().parse_snapshot(fixture('bybit-live-article.html'),
      'https://announcements.bybit.com/ru-RU/article/example/', 'article.art8fd5d30ccc24')
    assert p.reward_type==RewardType.POOL and p.reward_guaranteed is False
    assert '100 000 USDT' in p.reward
    assert '10 000' in p.raw_text and '7 рабочих дней' in p.raw_text
    assert len(p.steps)==4 and p.kyc and p.countries
    assert '8:00 UTC' in p.end_text and p.end_at is None
    assert any('CMS dates' in x for x in p.ambiguity)
    assert p.deposit is None and p.withdrawal is None


def test_identity_and_schema_fail_closed():
    a=BybitAdapter()
    with pytest.raises(SourceUnavailable): a.parse_snapshot(fixture('bybit-live-article.html'),'https://announcements.bybit.com/', 'wrong')
    with pytest.raises(SourceUnavailable): a.page_data('<html>Captcha or error</html>')


async def test_collect_reads_all_official_listing_ids_without_css_selector(monkeypatch):
    a=BybitAdapter(); data=listing(); expected=len(data['articleInitEntity']['list'])
    original=BybitAdapter.page_data(fixture('bybit-live-article.html'))
    async def fetch(url):
        if '/article/' not in url: return page(data)
        source=next(item for item in data['articleInitEntity']['list'] if item['url'] in url)
        copy=json.loads(json.dumps(original)); copy['articleObjectID']=source['objectID']
        copy['articleDetail']['title']=source['title']
        return page(copy)
    monkeypatch.setattr(a,'fetch',fetch)
    rows=await a.collect()
    assert len(rows)==expected==11
    assert len({p.source_id for p in rows})==11
    assert not a.verified and not a.selector


async def test_incomplete_listing_does_not_initialize_baseline(monkeypatch):
    a=BybitAdapter(); data=listing(); data['articleInitEntity']['total']=12
    async def fetch(url):
        data['initialPage']=int(parse_qs(urlsplit(url).query)['page'][0])
        return page(data)
    monkeypatch.setattr(a,'fetch',fetch)
    with pytest.raises(SourceUnavailable,match='incomplete'): await a.collect()


async def test_category_mismatch_rejected(monkeypatch):
    a=BybitAdapter(); data=listing();data['initialCategory']='news'
    async def fetch(url): return page(data)
    monkeypatch.setattr(a,'fetch',fetch)
    with pytest.raises(SourceUnavailable,match='category/page'): await a.collect()
