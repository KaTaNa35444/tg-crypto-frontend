"""Fail-closed official HTML adapters. No invented endpoints or live demo fallback."""
import asyncio
import hashlib
import os
import time
from datetime import datetime
from bs4 import BeautifulSoup
import httpx
from hunter.schema import Exchange, PromoInput, canonical_url

class SourceUnavailable(RuntimeError): pass

class OfficialAdapter:
    exchange: Exchange
    verified = False  # No live adapter was validated in the onboarding network.

    def __init__(self):
        prefix = self.exchange.value.upper()
        self.url = os.getenv(prefix + '_SOURCE_URL', '')
        self.selector = os.getenv(prefix + '_LINK_SELECTOR', '')
        self._last_request = 0.0
        self._next_allowed = 0.0

    def parse_snapshot(self, html: str, url: str, source_id: str | None = None) -> PromoInput:
        """Keep evidence, extract only explicitly labelled fields; always require review.

        Date text without an offset stays text. Selectors are deliberately generic;
        source-specific live selectors have not been verified.
        """
        url = canonical_url(url, self.exchange)
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script','style','nav','footer']): tag.decompose()
        article = soup.find('article') or soup.find('main') or soup
        heading = article.find('h1') or soup.find('title')
        if heading is None: raise ValueError('Missing publication title')
        raw = article.get_text('\n', strip=True)
        # Machine-readable labels in saved/manual snapshots; never infer financial promises.
        fields = {}
        for node in article.select('[data-promo-field]'):
            key = node.get('data-promo-field')
            if key in {'description','reward','audience','kyc','countries','deposit','volume',
                       'holding','limits','crediting','usage','withdrawal','start_text','end_text'}:
                fields[key] = node.get_text(' ', strip=True)
            elif key == 'steps':
                fields[key] = [x.get_text(' ', strip=True) for x in node.select('li')]
            elif key == 'reward_type': fields[key] = node.get_text(' ',strip=True)
        return PromoInput(exchange=self.exchange, source_id=source_id or hashlib.sha256(url.encode()).hexdigest(),
                          official_url=url, title=heading.get_text(' ', strip=True),
                          raw_text=raw, ambiguity=['Unverified HTML extraction; administrator must compare with official page'],
                          **fields)

    async def fetch(self, url):
        url = canonical_url(url, self.exchange)
        if time.monotonic()<self._next_allowed:
            raise SourceUnavailable('Source Retry-After delay is still active')
        # Explicit no-redirect policy avoids sending requests to unverified hosts.
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            for attempt in range(3):
                wait = max(0, 2 - (time.monotonic()-self._last_request))
                await asyncio.sleep(wait)
                self._last_request = time.monotonic()
                try:
                    response = await client.get(url, headers={'User-Agent':'CryptoPromoHunter/0.1 (public announcements only)'})
                    if response.status_code in {401,403}:
                        raise SourceUnavailable('Access denied; manual import required; no protection bypass')
                    if response.status_code == 429 or response.status_code >= 500:
                        retry=response.headers.get('Retry-After','')
                        delay=2**(attempt+1)
                        try:
                            delay=max(delay,float(retry))
                        except ValueError:
                            if retry:
                                try:
                                    from email.utils import parsedate_to_datetime
                                    from hunter.schema import utcnow
                                    delay=max(delay,(parsedate_to_datetime(retry)-utcnow()).total_seconds())
                                except (ValueError,TypeError): pass
                        self._next_allowed=time.monotonic()+delay
                        if attempt == 2: raise SourceUnavailable('Source throttled or unavailable')
                        if delay>60: raise SourceUnavailable('Source requested a long Retry-After; collection deferred')
                        await asyncio.sleep(delay)
                        continue
                    if response.is_redirect: raise SourceUnavailable('Redirect requires manual URL verification')
                    response.raise_for_status()
                    if len(response.content)>2_000_000: raise SourceUnavailable('Publication exceeds size limit')
                    return response.text
                except (httpx.TimeoutException,httpx.NetworkError):
                    if attempt==2: raise SourceUnavailable('Network timeout/failure') from None
                    await asyncio.sleep(2**(attempt+1))
        raise SourceUnavailable('Fetch failed')

    async def collect(self):
        if not self.url or not self.selector:
            raise SourceUnavailable('Manual import mode: no verified listing URL and link selector configured')
        html = await self.fetch(self.url)
        from urllib.parse import urljoin
        soup = BeautifulSoup(html,'html.parser')
        links = list(dict.fromkeys(canonical_url(urljoin(self.url,a['href']),self.exchange)
                     for a in soup.select(self.selector) if a.get('href')))
        if not links: raise SourceUnavailable('No publication links matched; initialization withheld')
        if len(links)>50: raise SourceUnavailable('Over 50 links: narrow selector; collection not silently truncated')
        result = []
        for link in links:
            result.append(self.parse_snapshot(await self.fetch(link),link))
        return result

class BitgetAdapter(OfficialAdapter): exchange = Exchange.BITGET
class MexcAdapter(OfficialAdapter): exchange = Exchange.MEXC

# The website's public server-rendered data was inspected on 2026-10-08.
# This does not make financial condition extraction eligible for auto-publication.
class BybitAdapter(OfficialAdapter):
    exchange = Exchange.BYBIT
    DEFAULT_URL = 'https://announcements.bybit.com/ru-RU/?category=latest_activities'

    def __init__(self):
        super().__init__()
        self.url = self.url or self.DEFAULT_URL

    @staticmethod
    def page_data(html):
        import json
        node = BeautifulSoup(html,'html.parser').find('script',id='__NEXT_DATA__')
        if node is None: raise SourceUnavailable('Bybit public page data missing; do not initialize source')
        try:
            data=json.loads(node.get_text())['props']['pageProps']
        except (ValueError,KeyError,TypeError):
            raise SourceUnavailable('Bybit public page schema changed') from None
        if not isinstance(data,dict): raise SourceUnavailable('Bybit public page schema changed')
        return data

    def parse_snapshot(self,html,url,source_id=None):
        import re
        from hunter.schema import RewardType
        data=self.page_data(html)
        detail=data.get('articleDetail')
        if not isinstance(detail,dict): raise SourceUnavailable('Bybit article data missing')
        title=detail.get('title')
        content=detail.get('content_html')
        if not isinstance(title,str) or not isinstance(content,str) or not content.strip():
            raise SourceUnavailable('Bybit article title or full text missing')
        actual_id=data.get('articleObjectID')
        if source_id and actual_id!=source_id: raise SourceUnavailable('Bybit publication identity mismatch')
        if not actual_id: raise SourceUnavailable('Bybit article identity missing')
        soup=BeautifulSoup(content,'html.parser')
        for tag in soup(['script','style']): tag.decompose()
        raw=soup.get_text('\n',strip=True)
        notes=['Full text collected automatically; financial conditions require administrator review']
        fields={}
        description=detail.get('description')
        if isinstance(description,str) and len(description.strip())<=400: fields['description']=description.strip()
        # Only explicit source phrases are extracted. Missing conditions remain unknown.
        if re.search(r'призов\w*\s+пул|prize\s+pool',title,re.I):
            fields.update(reward=title,reward_type=RewardType.POOL,reward_guaranteed=False)
        lines=[line.strip() for line in raw.splitlines() if line.strip()]
        steps=[line for line in lines if re.match(r'Шаг\s+\d+\b|Step\s+\d+\b',line,re.I)]
        if steps and len(steps)<=8 and all(len(x)<=220 for x in steps): fields['steps']=steps
        for key,pattern,limit in [('kyc',r'верификац|\bKYC\b',180),
                                  ('countries',r'запрещенн\w* юрисдикц|restricted jurisdictions',250)]:
            matches=[line for line in lines if re.search(pattern,line,re.I)]
            if len(matches)==1 and len(matches[0])<=limit: fields[key]=matches[0]
        period=[line for line in lines if re.search(r'Период промоакции|Campaign period|Event period',line,re.I)]
        if len(period)==1 and len(period[0])<=100: fields['end_text']=period[0]
        # Observed metadata end_time conflicts with explicit text (00:00Z vs 08:00 UTC).
        # Never schedule using CMS dates until a moderator verifies the campaign deadline.
        if detail.get('start_time') or detail.get('end_time'):
            notes.append('CMS dates may differ from campaign text; exact deadline requires manual confirmation')
        return PromoInput(exchange=self.exchange,source_id=actual_id,official_url=url,title=title,
            raw_text=raw,ambiguity=notes,**fields)

    async def collect(self):
        from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
        if not self.url: raise SourceUnavailable('Bybit source disabled')
        initial=canonical_url(self.url,self.exchange)
        if urlsplit(initial).hostname!='announcements.bybit.com':
            raise SourceUnavailable('Bybit structured collector requires the official announcements host')
        entries={}; total=None; locale=None
        for page in range(1,11):
            p=urlsplit(initial)
            query=dict(parse_qsl(p.query)); query.update(category='latest_activities',page=str(page))
            url=urlunsplit((p.scheme,p.netloc,p.path,urlencode(query),'') )
            data=self.page_data(await self.fetch(url))
            if data.get('initialCategory')!='latest_activities' or data.get('initialPage')!=page:
                raise SourceUnavailable('Bybit category/page mismatch; initialization withheld')
            current=data.get('articleInitEntity')
            if not isinstance(current,dict) or not isinstance(current.get('list'),list):
                raise SourceUnavailable('Bybit listing schema changed')
            current_total=current.get('total')
            if not isinstance(current_total,int) or current_total<=0 or current_total>200:
                raise SourceUnavailable('Bybit empty/oversized listing; initialization withheld')
            if total is not None and current_total!=total:
                raise SourceUnavailable('Bybit listing changed during pagination; retry next poll')
            total=current_total; locale=data.get('locale')
            if not isinstance(locale,str) or not re_locale(locale):
                raise SourceUnavailable('Bybit locale schema changed')
            before=len(entries)
            for item in current['list']:
                if not isinstance(item,dict) or item.get('category',{}).get('key')!='latest_activities':
                    raise SourceUnavailable('Bybit publication category mismatch')
                identity=item.get('objectID'); path=item.get('url')
                if not isinstance(identity,str) or not isinstance(path,str) or not path.startswith('/article/'):
                    raise SourceUnavailable('Bybit publication identity or URL schema changed')
                entries[identity]=canonical_url(f'https://announcements.bybit.com/{locale}{path}',self.exchange)
            if len(entries)==total: break
            if len(entries)>total or len(entries)==before:
                raise SourceUnavailable('Bybit pagination incomplete; initialization withheld')
        if len(entries)!=total: raise SourceUnavailable('Bybit page limit reached; initialization withheld')
        result=[]
        for identity,url in entries.items():
            result.append(self.parse_snapshot(await self.fetch(url),url,identity))
        return result


def re_locale(value):
    import re
    return re.fullmatch(r'[a-z]{2}(?:-[A-Z]{2,3})?',value) is not None

ADAPTERS = {a.exchange: a for a in (BybitAdapter, BitgetAdapter, MexcAdapter)}
