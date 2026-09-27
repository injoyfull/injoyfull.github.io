#!/usr/bin/env python3
"""인조이풀 레터 암호화 빌더

레터 HTML(과 그 회차 사진)을 반별 암호로 암호화해 /letters/data/ 에 넣는다.
암호를 모르면 어떤 글자도, 어떤 사진도 볼 수 없다(AES-256-GCM).

사용법:
  python3 tools/build-letters.py \
      --id kids-thu --name "키즈팝 목요반" --season "2026 F/W" \
      --password "…" \
      "레터1.html" "레터2.html" \
      --titles "제목1" "제목2" \
      --photo-dirs "사진/2608_1차" -          # 레터 순서대로, 사진 없으면 -

- 암호가 그대로면 예전 잠금(salt)을 그대로 쓰고, 내용이 바뀐 파일만 다시 만든다.
  (레터가 하나 늘 때마다 모든 레터·사진이 새로 커밋되는 것을 막는다)
- 사진은 긴 쪽 1440px JPEG로 줄인 뒤 한 장씩 잠가 /letters/data/p/<레터id>-NN.bin 으로 둔다.
"""
import argparse, base64, hashlib, json, os, re, secrets, subprocess, tempfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITER = 600_000          # PBKDF2 반복 — 오프라인 추측을 느리게 만든다
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'letters', 'data')
PHOTO_DIR = os.path.join(DATA, 'p')
CACHE = os.path.join(ROOT, 'tools', '.letters-build-cache.json')   # 로컬 전용: 출력 파일 ← 원본 해시
PHOTO_EXT = ('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp')
MAX_PX = 1440

b64  = lambda b: base64.b64encode(b).decode()
ub64 = lambda s: base64.b64decode(s)

def derive(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITER, 32)

def seal(key: bytes, plaintext: str) -> dict:
    iv = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(iv, plaintext.encode('utf-8'), None)
    return {'iv': b64(iv), 'ct': b64(ct)}

def seal_bytes(key: bytes, data: bytes) -> bytes:
    iv = secrets.token_bytes(12)
    return iv + AESGCM(key).encrypt(iv, data, None)      # 앞 12바이트 = iv

def text_of(s):
    s = re.sub(r'<br\s*/?>', ' ', s)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s)).strip()

def meta_from(path: str) -> tuple:
    html = open(path, encoding='utf-8').read()
    t = re.search(r'<title>(.*?)</title>', html, re.S)
    title = t.group(1).strip() if t else os.path.basename(path)
    title = re.sub(r'^.*?—\s*', '', title)                       # "인조이풀 레터 · … — 제목" → "제목"
    d = (re.search(r'class="date">\s*(20\d\d)\D+(\d{1,2})\D+(\d{1,2})', html)
         or re.search(r'(20\d\d)[.\-\s]*(\d{1,2})[.\-\s]*(\d{1,2})', html))
    date = f'{d.group(1)}.{int(d.group(2)):02d}.{int(d.group(3)):02d}' if d else ''
    return html, title, date

def photos_in(folder):
    if not folder or folder == '-' or not os.path.isdir(folder):
        return []
    fs = [f for f in os.listdir(folder) if f.lower().endswith(PHOTO_EXT) and not f.startswith(('.', '~'))]
    return [os.path.join(folder, f) for f in sorted(fs)]

def shrink(src: str) -> bytes:
    """긴 쪽 1440px JPEG(품질 75)로 — macOS 기본 sips 사용(HEIC도 됨)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'p.jpg')
        subprocess.run(['sips', '-s', 'format', 'jpeg', '-s', 'formatOptions', '75',
                        '-Z', str(MAX_PX), src, '--out', out], check=True, capture_output=True)
        return open(out, 'rb').read()

def fhash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--id', required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--season', default='')
    p.add_argument('--password', required=True)
    p.add_argument('--titles', nargs='*', default=None)
    p.add_argument('--photo-dirs', nargs='*', default=None)
    p.add_argument('files', nargs='+')
    a = p.parse_args()

    os.makedirs(PHOTO_DIR, exist_ok=True)
    mpath = os.path.join(DATA, 'manifest.json')
    manifest = json.load(open(mpath)) if os.path.exists(mpath) else {'classes': []}
    old = next((c for c in manifest['classes'] if c['id'] == a.id), None)
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    # 같은 암호면 예전 salt·확인값을 그대로 → 안 바뀐 파일은 다시 만들 필요가 없다
    key, salt, check = None, None, None
    if old:
        try:
            s = ub64(old['kdf']['salt']); k = derive(a.password, s)
            if AESGCM(k).decrypt(ub64(old['check']['iv']), ub64(old['check']['ct']), None) == b'in:JOYFULL':
                key, salt, check = k, s, old['check']
        except Exception:
            pass
    if key is None:                                   # 처음이거나 암호가 바뀜 → 전부 새로
        salt = secrets.token_bytes(16); key = derive(a.password, salt)
        check = seal(key, 'in:JOYFULL')
        cache = {k: v for k, v in cache.items() if not os.path.basename(k).startswith(a.id + '-')}
    tag = hashlib.sha256(salt).hexdigest()[:12]       # 캐시는 이 잠금에서만 유효

    keep = set()
    letters = []
    for i, f in enumerate(a.files):
        html, title, date = meta_from(f)
        if a.titles and i < len(a.titles): title = a.titles[i]
        lid = f'{a.id}-{i+1:02d}'
        out = os.path.join(DATA, f'{lid}.json'); keep.add(out)
        h = f'{tag}:{fhash(f)}'
        if cache.get(out) != h or not os.path.exists(out):
            with open(out, 'w') as fh:
                json.dump(seal(key, html), fh)
            cache[out] = h
            note = '새로 잠금'
        else:
            note = '그대로'

        # 이 회차 사진
        pdir = a.photo_dirs[i] if a.photo_dirs and i < len(a.photo_dirs) else None
        shots = photos_in(pdir)
        for n, src in enumerate(shots, 1):
            pout = os.path.join(PHOTO_DIR, f'{lid}-{n:02d}.bin'); keep.add(pout)
            ph = f'{tag}:{fhash(src)}'
            if cache.get(pout) != ph or not os.path.exists(pout):
                open(pout, 'wb').write(seal_bytes(key, shrink(src)))
                cache[pout] = ph

        entry = {'id': lid, 'title': title, 'date': date, 'file': f'{lid}.json'}
        if shots: entry['photos'] = len(shots)
        letters.append(entry)
        print(f'  ✓ {lid}  {date}  {title}  ({note}' + (f' · 사진 {len(shots)}장' if shots else '') + ')')

    # 이 반의 쓰지 않게 된 파일 정리 (레터가 빠졌거나 사진 수가 줄었을 때)
    for folder in (DATA, PHOTO_DIR):
        for fn in os.listdir(folder):
            fp = os.path.join(folder, fn)
            if fn.startswith(a.id + '-') and fp not in keep:
                os.remove(fp); cache.pop(fp, None)

    manifest['classes'] = [c for c in manifest['classes'] if c['id'] != a.id]
    manifest['classes'].append({
        'id': a.id, 'name': a.name, 'season': a.season,
        'kdf': {'salt': b64(salt), 'iter': ITER},
        'check': check,                          # 암호 확인용 — 맞아야 복호화된다
        'letters': letters,
    })
    manifest['classes'].sort(key=lambda c: c['id'])
    json.dump(manifest, open(mpath, 'w'), ensure_ascii=False, indent=1)
    json.dump(cache, open(CACHE, 'w'), ensure_ascii=False, indent=1)
    total = sum(l.get('photos', 0) for l in letters)
    print(f'\n{a.name} · {len(letters)}통 · 사진 {total}장 잠금 완료.')

if __name__ == '__main__':
    main()
