"""Read-only source diagnostics: no Telegram client, database writes or publication."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from hunter.adapters import ADAPTERS, SourceUnavailable
from hunter.schema import Exchange, canonical_url


def inspect_links(html, url, exchange, selector=None):
    url=canonical_url(url,exchange)
    matches=BeautifulSoup(html,'html.parser').select(selector or 'a[href]')
    links={}; rejected=0
    for anchor in matches:
        if not anchor.get('href'): continue
        try: target=canonical_url(urljoin(url,anchor['href']),exchange)
        except ValueError:
            rejected+=1; continue
        if target!=url: links.setdefault(target,anchor.get_text(' ',strip=True)[:180])
    return dict(mode='selector_check' if selector else 'discovery_only_not_a_verified_feed',
        matched_elements=len(matches),official_links=len(links),rejected_links=rejected,
        examples=[dict(url=u,title=t) for u,t in list(links.items())[:20]],
        next_step='Verify campaign links and article HTML before enabling the adapter.')


async def check(args):
    try:
        exchange=Exchange(args.exchange)
        url=canonical_url(args.url,exchange)
        html=await ADAPTERS[exchange]().fetch(url)
        if args.save_html:
            with Path(args.save_html).open('x',encoding='utf-8') as output: output.write(html)
        report=inspect_links(html,url,exchange,args.selector)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if not report['official_links']:
            print('No official links detected. JavaScript rendering or another source may be required.')
            return 2
        return 0
    except SourceUnavailable as exc:
        print('Source unavailable: '+str(exc)); return 2
    except Exception as exc:
        print('Source diagnostic failed: '+type(exc).__name__); return 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exchange',choices=[x.value for x in Exchange],required=True)
    parser.add_argument('--url',required=True)
    parser.add_argument('--selector',help='Candidate CSS selector to test')
    parser.add_argument('--save-html',help='New file for public HTML; existing files are preserved')
    raise SystemExit(asyncio.run(check(parser.parse_args())))

if __name__=='__main__': main()
