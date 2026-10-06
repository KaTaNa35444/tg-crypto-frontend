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

class BybitAdapter(OfficialAdapter): exchange = Exchange.BYBIT
class BitgetAdapter(OfficialAdapter): exchange = Exchange.BITGET
class MexcAdapter(OfficialAdapter): exchange = Exchange.MEXC
ADAPTERS = {a.exchange: a for a in (BybitAdapter,BitgetAdapter,MexcAdapter)}
