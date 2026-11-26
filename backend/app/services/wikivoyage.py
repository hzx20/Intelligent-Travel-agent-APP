"""Read factual listing names from public Chinese Wikivoyage articles."""
from dataclasses import dataclass
from html.parser import HTMLParser
from threading import BoundedSemaphore, Lock
from unicodedata import category, normalize
from urllib.parse import unquote, urlsplit

from cachetools import TTLCache
import httpx

from app.services.supplier_http import SupplierError, request_json


HOST = 'zh.wikivoyage.org'
ENDPOINT = f'https://{HOST}/w/api.php'
LICENSE_URL = 'https://creativecommons.org/licenses/by-sa/4.0/'
_PATHS = ('/wiki/', '/zh/', '/zh-hans/', '/zh-hant/', '/zh-cn/', '/zh-hk/', '/zh-tw/', '/zh-mo/', '/zh-sg/', '/zh-my/')
_AGENT = 'IntelligentTravelAgent/1.0 (https://134.175.137.18/travel/)'
_LIMIT = 50
_VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
_cache = TTLCache(maxsize=128, ttl=900)
_lock = Lock()
_capacity = BoundedSemaphore(1)


def article_title(value):
    if not isinstance(value, str):
        raise ValueError('Expected an article title')
    title = normalize('NFC', value).replace('_', ' ').strip()
    if (
        not 1 <= len(title) <= 160
        or set(normalize('NFKC', title)).intersection(':%?#\\')
        or any(part in ('', '.', '..') for part in title.split('/'))
        or any(category(char).startswith('C') for char in title)
    ):
        raise ValueError('Only main-namespace articles are supported')
    return title


def title_from_url(value):
    try:
        parts = urlsplit(value.strip())
        if parts.scheme != 'https' or parts.netloc.lower() != HOST or parts.query:
            raise ValueError
        for prefix in _PATHS:
            if parts.path.startswith(prefix):
                return article_title(unquote(parts.path[len(prefix):], errors='strict'))
    except ValueError:
        pass
    raise SupplierError('请粘贴中文维基导游的 HTTPS 目的地页面链接', 422)


class _ListingNames(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []
        self.names = []
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        if self.depth and tag not in _VOID:
            self.depth += 1
        elif tag == 'span' and 'listing-name' in (dict(attrs).get('class') or '').split():
            self.depth = 1
            self.parts.clear()

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if not self.depth or tag in _VOID:
            return
        self.depth -= 1
        if self.depth:
            return
        name = ' '.join(''.join(self.parts).split())
        self.parts.clear()
        identity = normalize('NFKC', name).casefold()
        if (1 <= len(name) <= 160 and identity not in self.seen and len(self.names) < _LIMIT
                and not any(category(char).startswith('C') for char in name)):
            self.seen.add(identity)
            self.names.append(name)


@dataclass(frozen=True)
class PublicGuide:
    title: str
    revision: int
    names: tuple[str, ...]

    @property
    def source_url(self):
        return f'https://{HOST}/w/index.php?oldid={self.revision}'


def _fetch(title, client):
    data = request_json(
        client, 'GET', ENDPOINT, max_bytes=512 * 1024,
        headers={'User-Agent': _AGENT},
        params={'action': 'parse', 'page': title, 'prop': 'text|revid', 'format': 'json',
                'formatversion': 2, 'variant': 'zh-hans', 'disableeditsection': 1},
    )
    if isinstance(data.get('error'), dict) and data['error'].get('code') == 'missingtitle':
        raise SupplierError('这篇维基导游攻略不存在', 404)
    try:
        page = data['parse']
        title = article_title(page['title'])
        revision = page['revid']
        if type(revision) is not int or not 1 <= revision < 2**53 or not isinstance(page['text'], str):
            raise ValueError
        parser = _ListingNames()
        parser.feed(page['text'])
        parser.close()
    except (KeyError, TypeError, ValueError):
        raise SupplierError('维基导游返回的攻略无法识别，请稍后重试') from None
    return PublicGuide(title, revision, tuple(parser.names))


def fetch_guide(title, *, client=None):
    with _lock:
        cached = _cache.get(title)
    if cached is not None:
        return cached
    if not _capacity.acquire(blocking=False):
        raise SupplierError('维基导游查询繁忙，请稍后重试', 429)
    try:
        # A result may have been stored while this caller was trying to acquire capacity.
        with _lock:
            cached = _cache.get(title)
        if cached is not None:
            return cached
        if client is None:
            with httpx.Client(timeout=httpx.Timeout(8, connect=4), trust_env=False, follow_redirects=False) as connection:
                result = _fetch(title, connection)
        else:
            result = _fetch(title, client)
        with _lock:
            _cache[title] = result
        return result
    finally:
        _capacity.release()
