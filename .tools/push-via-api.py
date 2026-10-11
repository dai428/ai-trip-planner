"""在 github.com 被网络封锁的环境下，改用 GitHub Git Data API 完成推送。

【为什么需要这个脚本】
    本机出网白名单只放行 api.github.com，github.com 被硬封锁
    （git smart-HTTP 端点直接超时），因此 git push 走不通。
    REST API 可达，于是用 Git Data API 组装提交。

【空仓库的死循环与破解】
    /git/blobs 在**完全空仓库**上返回 409（Git Repository is empty），
    而建 root commit 又需要 tree、建 tree 需要 blob。
    所以先调 Contents API 写一个文件把仓库"激活"（产生初始提交），
    再把完整内容作为它的后继提交。

结果：远端历史 = 初始提交 + 完整提交（2 个）。内容树与本地完全一致。

用法：GH_TOKEN=<token> python push-via-api.py <owner/repo>
"""

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OWNER_REPO = sys.argv[1] if len(sys.argv) > 1 else 'dai428/ai-trip-planner'
TOKEN = os.environ.get('GH_TOKEN', '')
ROOT = Path(__file__).resolve().parent.parent
API = 'https://api.github.com'

if not TOKEN:
    sys.exit('缺少 GH_TOKEN')

SKIP_PREFIXES = ('.venv/', 'node_modules/', 'dist/', '__pycache__/', 'backend/db/')
INIT_FILE = '.gitignore'          # 用来激活空仓库的初始文件
INIT_MESSAGE = 'chore: 初始化仓库'


def git(*args):
    # encoding='utf-8' 必须显式指定：中文 Windows 下 text=True 会按 GBK 解码，
    # 会把 git 输出的 UTF-8 路径（如 data/guides/三亚.json）解成乱码。
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True,
                          text=True, encoding='utf-8', check=True).stdout.strip()


def api(method, path, payload=None):
    req = urllib.request.Request(
        f'{API}{path}',
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            'Authorization': f'Bearer {TOKEN}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'push-via-api',
            'Content-Type': 'application/json',
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{method} {path} -> HTTP {e.code}: {e.read().decode()[:400]}') from None


# ---------- 1. 读取本地提交的元数据，用于精确复现 ----------
fmt = '%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%H'
an, ae, adate, cn, ce, cdate, local_sha = git('log', '-1', f'--format={fmt}').split('\x00')
message = git('log', '-1', '--format=%B')
# 必须加 core.quotepath=false + -z：否则 git 会把含非 ASCII 的路径做 C 引用
# （如 "backend/app/data/guides/\344\270\211\344\272\232.json"），
# 路径连双引号一起被当成字面量，读文件时报 OSError [Errno 22]。
# -z 用 NUL 分隔，天然支持含空白/特殊字符的文件名。
files = [f for f in git('-c', 'core.quotepath=false', 'ls-files', '-z').split('\x00')
         if f and not f.startswith(SKIP_PREFIXES)]

print(f'本地提交: {local_sha[:12]}')
print(f'作者: {an} <{ae}>  {adate}')
print(f'待上传文件: {len(files)}')

# ---------- 2. 用 Contents API 激活空仓库（同时得到父提交） ----------
try:
    ref = api('GET', f'/repos/{OWNER_REPO}/git/ref/heads/main')
    parent_sha = ref['object']['sha']
    print(f'仓库已有初始提交，父提交: {parent_sha[:12]}')
except RuntimeError as e:
    # 只有 404（空仓库没有 main 分支）才走激活流程；
    # 401/403（token 失效或无权限）必须直接抛出，否则会伪装成一个看不懂的后续错误。
    if 'HTTP 404' not in str(e):
        raise
    init_content = (ROOT / INIT_FILE).read_text(encoding='utf-8')
    r = api('PUT', f'/repos/{OWNER_REPO}/contents/{INIT_FILE}', {
        'message': INIT_MESSAGE,
        'content': base64.b64encode(init_content.encode()).decode(),
        'branch': 'main',
    })
    parent_sha = r['commit']['sha']
    print(f'已激活空仓库，初始提交: {parent_sha[:12]}')

# ---------- 3. 并发上传 blobs ----------
blob_shas = {}


def upload(path):
    data = (ROOT / path).read_bytes()
    r = api('POST', f'/repos/{OWNER_REPO}/git/blobs',
            {'content': base64.b64encode(data).decode(), 'encoding': 'base64'})
    return path, r['sha']


with ThreadPoolExecutor(max_workers=8) as ex:
    for i, (path, sha) in enumerate(ex.map(upload, files), 1):
        blob_shas[path] = sha
        if i % 20 == 0 or i == len(files):
            print(f'  已上传 blob {i}/{len(files)}')

tree_entries = [{'path': p, 'mode': '100644', 'type': 'blob', 'sha': s}
                for p, s in blob_shas.items()]

# ---------- 4. 建 tree ----------
tree = api('POST', f'/repos/{OWNER_REPO}/git/trees', {'tree': tree_entries})
print(f'树 SHA: {tree["sha"]}')

# ---------- 5. 建 commit（元数据沿用本地提交） ----------
commit = api('POST', f'/repos/{OWNER_REPO}/git/commits', {
    'message': message,
    'tree': tree['sha'],
    'parents': [parent_sha],
    'author': {'name': an, 'email': ae, 'date': adate},
    'committer': {'name': cn, 'email': ce, 'date': cdate},
})
remote_sha = commit['sha']
print(f'远端提交 SHA: {remote_sha}')

# ---------- 6. 移动分支指针 ----------
api('PATCH', f'/repos/{OWNER_REPO}/git/refs/heads/main', {'sha': remote_sha, 'force': True})
print('已更新 refs/heads/main')

# ---------- 7. 校验远端树与本地一致 ----------
remote_files = api('GET', f'/repos/{OWNER_REPO}/git/trees/{remote_sha}?recursive=1')['tree']
remote_paths = sorted(t['path'] for t in remote_files if t['type'] == 'blob')
local_paths = sorted(files)
print(f'\n远端文件数: {len(remote_paths)}   本地文件数: {len(local_paths)}')
print(f'路径集合一致: {"是 ✓" if remote_paths == local_paths else "否 ✗"}')
if remote_paths != local_paths:
    only_r = set(remote_paths) - set(local_paths)
    only_l = set(local_paths) - set(remote_paths)
    if only_r:
        print('  仅远端有:', sorted(only_r)[:10])
    if only_l:
        print('  仅本地有:', sorted(only_l)[:10])

print(f'\n完成: https://github.com/{OWNER_REPO}')
