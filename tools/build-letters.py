#!/usr/bin/env python3
"""인조이풀 레터 암호화 빌더

레터 HTML을 반별 암호로 암호화해 /letters/data/ 에 넣는다.
암호를 모르면 어떤 글자도 읽을 수 없다(AES-256-GCM).

사용법:
  python3 tools/build-letters.py \
      --id kids-thu --name "키즈팝 목요반" --season "2026 F/W" \
      --password "목요반-가을-7291" \
      "레터1.html" "레터2.html"

제목·날짜는 레터 <title>과 파일명에서 자동으로 뽑되, --titles 로 직접 줄 수도 있다.
같은 --id 로 다시 돌리면 그 반의 데이터가 통째로 새로 만들어진다(암호 교체도 이 방법).
"""
import argparse, base64, hashlib, json, os, re, secrets, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITER = 600_000          # PBKDF2 반복 — 오프라인 추측을 느리게 만든다
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'letters', 'data')

b64  = lambda b: base64.b64encode(b).decode()

def derive(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITER, 32)

def seal(key: bytes, plaintext: str) -> dict:
    iv = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(iv, plaintext.encode('utf-8'), None)
    return {'iv': b64(iv), 'ct': b64(ct)}

def meta_from(path: str) -> tuple:
    html = open(path, encoding='utf-8').read()
    t = re.search(r'<title>(.*?)</title>', html, re.S)
    title = t.group(1).strip() if t else os.path.basename(path)
    title = re.sub(r'^.*?—\s*', '', title)                       # "인조이풀 레터 · … — 제목" → "제목"
    d = re.search(r'(20\d\d)[.\-\s]*(\d{1,2})[.\-\s]*(\d{1,2})', html)
    date = f'{d.group(1)}.{int(d.group(2)):02d}.{int(d.group(3)):02d}' if d else ''
    return html, title, date

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--id', required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--season', default='')
    p.add_argument('--password', required=True)
    p.add_argument('--titles', nargs='*', default=None)
    p.add_argument('files', nargs='+')
    a = p.parse_args()

    os.makedirs(DATA, exist_ok=True)
    salt = secrets.token_bytes(16)
    key = derive(a.password, salt)

    letters = []
    for i, f in enumerate(a.files):
        html, title, date = meta_from(f)
        if a.titles and i < len(a.titles): title = a.titles[i]
        lid = f'{a.id}-{i+1:02d}'
        with open(os.path.join(DATA, f'{lid}.json'), 'w') as fh:
            json.dump(seal(key, html), fh)
        letters.append({'id': lid, 'title': title, 'date': date, 'file': f'{lid}.json'})
        print(f'  ✓ {lid}  {date}  {title}  ({len(html)//1024}KB → 암호문)')

    mpath = os.path.join(DATA, 'manifest.json')
    manifest = json.load(open(mpath)) if os.path.exists(mpath) else {'classes': []}
    manifest['classes'] = [c for c in manifest['classes'] if c['id'] != a.id]
    manifest['classes'].append({
        'id': a.id, 'name': a.name, 'season': a.season,
        'kdf': {'salt': b64(salt), 'iter': ITER},
        'check': seal(key, 'in:JOYFULL'),      # 암호 확인용 — 맞아야 복호화된다
        'letters': letters,
    })
    manifest['classes'].sort(key=lambda c: c['id'])
    json.dump(manifest, open(mpath, 'w'), ensure_ascii=False, indent=1)
    print(f'\n{a.name} · {len(letters)}통 · 암호 「{a.password}」 로 잠갔습니다.')

if __name__ == '__main__':
    main()
