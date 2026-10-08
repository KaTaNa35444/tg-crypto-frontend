from argparse import Namespace
from hunter.source_check import inspect_links,check
from hunter.schema import Exchange
from hunter.adapters import SourceUnavailable,BybitAdapter


def test_discovery_is_not_claimed_as_feed():
    html='<nav><a href="/help">Help</a></nav><article><a href="/example?utm_source=test">Example</a><a href="https://evil.test/">Invalid</a><a href="/example#top">Duplicate</a></article>'
    report=inspect_links(html,'https://announcements.bybit.com/',Exchange.BYBIT)
    assert report['mode']=='discovery_only_not_a_verified_feed'
    assert report['official_links']==2 and report['rejected_links']==1
    selected=inspect_links(html,'https://announcements.bybit.com/',Exchange.BYBIT,'article a[href]')
    assert selected['official_links']==1 and selected['mode']=='selector_check'


def test_javascript_only_page_does_not_invent_publications():
    report=inspect_links('<script>render()</script><div id="root"></div>','https://announcements.bybit.com/',Exchange.BYBIT)
    assert report['official_links']==0


async def test_diagnostic_saves_without_overwriting(monkeypatch,tmp_path,capsys):
    async def fetch(self,url): return '<a href="/example">Example</a>'
    monkeypatch.setattr(BybitAdapter,'fetch',fetch)
    path=tmp_path/'bybit.html'
    args=Namespace(exchange='bybit',url='https://announcements.bybit.com/',selector=None,save_html=str(path))
    assert await check(args)==0
    original=path.read_text()
    assert await check(args)==2
    assert path.read_text()==original and 'FileExistsError' in capsys.readouterr().out


async def test_denial_does_not_become_mock_success(monkeypatch,capsys):
    async def fetch(self,url): raise SourceUnavailable('Access denied; manual import required')
    monkeypatch.setattr(BybitAdapter,'fetch',fetch)
    args=Namespace(exchange='bybit',url='https://announcements.bybit.com/',selector=None,save_html=None)
    assert await check(args)==2 and 'Source unavailable: Access denied' in capsys.readouterr().out
