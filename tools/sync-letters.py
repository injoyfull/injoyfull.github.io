#!/usr/bin/env python3
"""인조이풀 레터 자동 동기화

「피드백 레터」 폴더(목요반/·화요반/ …)에 레터 HTML이 올라오면
그 반만 다시 암호화해서 /letters/data/ 에 넣고, 커밋·푸시까지 한다.
macOS launchd(WatchPaths)가 폴더 변화를 보고 이 스크립트를 부른다.

  python3 tools/sync-letters.py            # 바뀐 반이 있으면 암호화 → 커밋 → 푸시
  python3 tools/sync-letters.py --dry-run  # 무엇을 할지만 보여준다
  python3 tools/sync-letters.py --init     # 지금 상태를 '이미 올라간 것'으로 기록만 한다

반 판별: 폴더 이름(목요반/화요반/수요반/금요반) 또는 파일 이름에 든 반 이름.
제목: 편지의 큰 제목(h1) → tools/letters.local.json 의 titles 예외 → <title> 순.
날짜: 편지 안 첫 날짜. 순서는 날짜 → 파일 이름.
암호: tools/letters.local.json (저장소 밖). 암호가 없는 반은 올리지 않고 알린다.
"""
import os, sys, re, json, html, hashlib, subprocess, time, datetime, importlib.util, unicodedata

ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
SRC   = '/Users/hanna/Workspace/inJOYFULL/06_운영·행정/피드백 레터'
LOCAL = os.path.join(TOOLS, 'letters.local.json')
STATE = os.path.join(TOOLS, '.letters-sync-state.json')
LOGF  = os.path.join(TOOLS, 'letters-sync.log')
LOCK  = os.path.join(TOOLS, '.letters-sync.lock')
SEASON = '2026 F/W'
CLASSES = {  # 폴더/파일 이름의 반 → (id, 표시 이름)
    '화요반': ('kids-tue', '키즈팝 화요반'),
    '수요반': ('kids-wed', '키즈팝 수요반'),
    '목요반': ('kids-thu', '키즈팝 목요반'),
    '금요반': ('kids-fri', '키즈팝 금요반'),
}


def log(msg):
    line = f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}'
    print(line)
    with open(LOGF, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def notify(title, msg):
    try:
        subprocess.run(['osascript', '-e', f'display notification "{msg}" with title "{title}"'],
                       timeout=10, capture_output=True)
    except Exception:
        pass


def load_json(p, default):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def build_module():
    spec = importlib.util.spec_from_file_location('build_letters', os.path.join(TOOLS, 'build-letters.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def detect_class(path):
    # macOS는 파일 이름을 자모 분리형(NFD)으로 저장하므로, 비교 전에 완성형(NFC)으로 맞춘다
    rel = unicodedata.normalize('NFC', os.path.relpath(path, SRC))
    top = rel.split(os.sep)[0]
    for key in CLASSES:
        if key in top:                       # 폴더 이름
            return key
    for key in CLASSES:
        if key in os.path.basename(rel):     # 파일 이름
            return key
    return None


def scan():
    found = {}
    for dp, dns, fns in os.walk(SRC):
        dns[:] = [d for d in dns if not d.startswith('.')]
        for fn in fns:
            if not fn.lower().endswith('.html') or fn.startswith(('.', '~$')):
                continue
            p = os.path.join(dp, fn)
            key = detect_class(p)
            if key:
                found.setdefault(key, []).append(p)
    return found


def h1_title(text):
    m = re.search(r'<h1[^>]*>(.*?)</h1>', text, re.S)
    if not m:
        return ''
    t = re.sub(r'<br\s*/?>', ' ', m.group(1))
    t = re.sub(r'<[^>]+>', '', t)
    return html.unescape(re.sub(r'\s+', ' ', t)).strip()


def title_of(path, text, fallback, overrides):
    name = unicodedata.normalize('NFC', os.path.basename(path))
    if name in overrides:
        return overrides[name]
    t = h1_title(text)
    if t and len(t) <= 40:
        return t
    t = re.sub(r'^.*?·\s*[가-힣]+반\s*', '', fallback)   # "#002 · 목요반 제목" → "제목"
    return t or name


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def signature(paths):
    names = sorted(f'{unicodedata.normalize("NFC", os.path.basename(p))}:{sha(p)}' for p in paths)
    return hashlib.sha256('\n'.join(names).encode()).hexdigest()


def wait_settled(paths, timeout=90):
    """복사 중인 파일이 다 들어올 때까지 기다린다(크기·수정시각이 6초 동안 그대로면 끝)."""
    def snap():
        return [(p, os.path.getsize(p), os.path.getmtime(p)) for p in paths if os.path.exists(p)]
    last, t0 = snap(), time.time()
    while time.time() - t0 < timeout:
        time.sleep(6)
        cur = snap()
        if cur == last and all(s > 0 for _, s, _ in cur):
            return True
        last = cur
    return False


def git(*args, check=True):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=check)


def main():
    dry, init = '--dry-run' in sys.argv, '--init' in sys.argv
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 600:
        log('다른 동기화가 진행 중이라 건너뜀')
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        local = load_json(LOCAL, {})
        pw = local.get('passwords', {})
        overrides = local.get('titles', {})
        state = load_json(STATE, {})
        found = scan()
        if not found:
            log('레터 파일을 찾지 못함')
            return
        B = build_module()
        changed = []
        for key, paths in sorted(found.items()):
            cid, cname = CLASSES[key]
            sig = signature(paths)
            if state.get(cid) == sig:
                continue
            if init:
                state[cid] = sig
                log(f'{cname}: 현재 {len(paths)}통을 올라간 상태로 기록')
                continue
            if cid not in pw:
                log(f'{cname}: 암호가 없어 올리지 않음 — tools/letters.local.json 에 "{cid}" 암호를 넣어주세요')
                notify('인조이풀 레터', f'{cname} 암호가 없어 올리지 못했어요')
                continue
            if not wait_settled(paths):
                log(f'{cname}: 파일이 아직 복사 중인 것 같아 이번엔 건너뜀')
                continue
            items = []
            for p in paths:
                text, tt, date = B.meta_from(p)
                items.append((date, os.path.basename(p), p, title_of(p, text, tt, overrides)))
            items.sort()
            log(f'{cname}: {len(items)}통 → ' + ' / '.join(f'{d} {t}' for d, _, _, t in items))
            if dry:
                continue
            cmd = [sys.executable, os.path.join(TOOLS, 'build-letters.py'), '--id', cid, '--name', cname,
                   '--season', SEASON, '--password', pw[cid],
                   *[p for _, _, p, _ in items], '--titles', *[t for _, _, _, t in items]]
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                log(f'{cname}: 암호화 실패\n{r.stderr[-800:]}')
                notify('인조이풀 레터', f'{cname} 암호화에 실패했어요')
                continue
            state[cid] = sig
            changed.append((cname, items))
        if dry:
            log('dry-run 끝 (아무것도 바꾸지 않음)')
            return
        json.dump(state, open(STATE, 'w'), ensure_ascii=False, indent=1)
        if init:
            log('초기 상태 기록 완료')
            return
        if not changed:
            log('바뀐 레터 없음')
            return
        git('add', 'letters/data')
        if git('diff', '--cached', '--quiet', check=False).returncode == 0:
            log('암호문은 다시 만들었지만 내용 차이가 없어 커밋하지 않음')
            return
        summary = ' · '.join(f'{n} {len(it)}통(최신: {it[-1][3]})' for n, it in changed)
        git('commit', '-q', '-m', f'인조이풀 레터 자동 업데이트 — {summary}',
            '-m', '폴더 감시 자동 동기화 (tools/sync-letters.py)')
        p = git('push', '-q', check=False)
        if p.returncode != 0:
            log(f'푸시 실패: {p.stderr[-500:]}')
            notify('인조이풀 레터', '푸시에 실패했어요. 터미널에서 git push 를 확인해 주세요')
            return
        log(f'올림: {summary}')
        notify('인조이풀 레터 올라감', summary + ' — 2~3분 뒤 홈페이지에 보여요')
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == '__main__':
    main()
