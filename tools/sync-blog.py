#!/usr/bin/env python3
"""행복맛집 「오늘의 메뉴」 자동 추가

네이버 블로그 RSS를 읽어, 「생각서랍」 카테고리에서 제목이 「… — ○○의 맛」으로 끝나는
새 글을 menu/index.html 의 오늘의 메뉴 맨 위에 한 줄씩 넣는다.

- 이미 사이트에 손으로 올린 맛(같은 이름)은 건너뛴다.
- 한 번 들어온 글은 tools/blog-tastes.json 에 쌓아 두어, RSS에서 밀려나도 사라지지 않는다.
- 바뀐 게 있으면 종료 코드 0과 함께 "changed" 를 출력한다 (GitHub Actions 가 커밋 여부를 판단).

  python3 tools/sync-blog.py
"""
import json, os, re, html, sys, urllib.request
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MENU = os.path.join(ROOT, 'menu', 'index.html')
STORE = os.path.join(ROOT, 'tools', 'blog-tastes.json')
RSS = 'https://rss.blog.naver.com/injoyfull.xml'
CATEGORY = '생각서랍'
START, END = '<!-- auto:taste:start', '<!-- auto:taste:end -->'
TITLE_RE = re.compile(r'^(?P<desc>.+?)\s*[—–-]\s*(?P<name>[^—–-]+의 맛)\s*$')


def clean(x):
    return html.unescape(re.sub(r'<!\[CDATA\[|\]\]>', '', x or '')).strip()


def fetch_rss():
    req = urllib.request.Request(RSS, headers={'User-Agent': 'injoyfull-site-sync/1.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', 'replace')


def parse(xml):
    out = []
    for it in re.findall(r'<item>(.*?)</item>', xml, re.S):
        g = lambda tag: clean((re.search(rf'<{tag}>(.*?)</{tag}>', it, re.S) or [None, ''])[1])
        title, link, cat, date = g('title'), g('link'), g('category'), g('pubDate')
        if cat != CATEGORY:
            continue
        m = TITLE_RE.match(title)
        if not m:
            continue
        link = re.sub(r'\?.*$', '', link)                       # 추적용 꼬리 제거
        try:
            iso = parsedate_to_datetime(date).date().isoformat()
        except Exception:
            iso = ''
        out.append({'name': m['name'].strip(), 'desc': m['desc'].strip(), 'url': link, 'date': iso})
    return out


def manual_names(page):
    """자동 영역 밖에 이미 있는 맛 이름들"""
    a, b = page.index(START), page.index(END) + len(END)
    outside = page[:a] + page[b:]
    sec = outside[outside.index('<p class="menu__course">오늘의 메뉴</p>'):]
    sec = sec[:sec.index('</ul>')]
    return set(re.findall(r'<span class="mrow__name">([^<]+)</span>', sec))


def render(items):
    rows = []
    for t in items:
        rows.append(f'''        <li>
          <a class="mrow" href="{html.escape(t['url'])}" rel="noopener" target="_blank">
            <span class="mrow__name">{html.escape(t['name'])}</span>
            <span class="mrow__dots" aria-hidden="true"></span>
            <span class="mrow__desc">{html.escape(t['desc'])} <i class="mrow__arrow">→</i></span>
          </a>
        </li>''')
    return '\n'.join(rows)


def main():
    page = open(MENU, encoding='utf-8').read()
    store = json.load(open(STORE, encoding='utf-8')) if os.path.exists(STORE) else []
    known = {t['url'] for t in store}
    skip = manual_names(page)

    for t in parse(fetch_rss()):
        if t['url'] in known or t['name'] in skip:
            continue
        store.append(t)
        known.add(t['url'])
        print('new taste:', t['name'], '—', t['desc'])

    store.sort(key=lambda t: t['date'], reverse=True)           # 최신이 위로
    visible = [t for t in store if t['name'] not in skip]

    a = page.index(START)
    a_end = page.index('-->', a) + 3
    b = page.index(END)
    block = '\n' + (render(visible) + '\n' if visible else '') + '        '
    new_page = page[:a_end] + block + page[b:]

    changed = new_page != page
    json.dump(store, open(STORE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    open(STORE, 'a', encoding='utf-8').write('\n')
    if changed:
        open(MENU, 'w', encoding='utf-8').write(new_page)
        print('changed')
    else:
        print('no change')


if __name__ == '__main__':
    sys.exit(main())
