"""探针 2：把"顶层形状"这件事彻底钉死。

探针 1 的结论与预期不符：声明了 type=object，返回的却是 [{...}]。
在写正式代码前必须回答三个问题：
  ① 这个"包一层数组"的行为是稳定的，还是偶发？
  ② 把 schema 顶层直接声明为数组，会不会反而合规？
  ③ 嵌套的复杂 schema（行程：天 × 活动）会不会被降级/截断？
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app import config  # noqa: E402

MSGS = [
    {'role': 'system', 'content': '你是行程规划助手，只输出结构化数据，不要解释。'},
    {'role': 'user', 'content': '从候选中挑 3 个景点：故宫、天坛、颐和园、长城、北海公园。城市北京。'},
]


def call(body):
    body = {'model': config.LLM_MODEL, 'messages': MSGS, **body}
    req = urllib.request.Request(
        f'{config.LLM_API_BASE}/chat/completions',
        data=json.dumps(body).encode(),
        headers={'Authorization': f'Bearer {config.LLM_API_KEY}',
                 'Content-Type': 'application/json'}, method='POST')
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}: {e.read().decode()[:160]}', time.time() - t0
    return data['choices'][0]['message'].get('content'), None, time.time() - t0


OBJ_SCHEMA = {'type': 'object', 'properties': {
    'city': {'type': 'string'}, 'picks': {'type': 'array', 'items': {'type': 'string'}}},
    'required': ['city', 'picks'], 'additionalProperties': False}

ARR_SCHEMA = {'type': 'array', 'items': OBJ_SCHEMA}

# ---- ① 稳定性：同一请求连打 5 次，看顶层形状是否一致 ----
print('=== ① 稳定性测试：object schema × 5 次 ===')
shapes = Counter()
for i in range(5):
    content, err, dt = call({'response_format': {
        'type': 'json_schema',
        'json_schema': {'name': 't', 'schema': OBJ_SCHEMA}}, 'enable_thinking': False})
    if err:
        shapes[f'ERR({err[:40]})'] += 1
        continue
    try:
        p = json.loads(content)
        shapes[type(p).__name__] += 1
    except Exception:
        shapes['非法JSON'] += 1
print('  顶层类型分布:', dict(shapes))

# ---- ② 顶层声明为数组，是否合规 ----
print('=== ② 数组 schema 测试 × 2 次 ===')
for i in range(2):
    content, err, dt = call({'response_format': {
        'type': 'json_schema',
        'json_schema': {'name': 't', 'schema': ARR_SCHEMA}}, 'enable_thinking': False})
    if err:
        print(f'  第{i+1}次 ERR {err}')
        continue
    p = json.loads(content)
    print(f'  第{i+1}次 顶层={type(p).__name__}  '
          f'元素数={len(p) if isinstance(p, list) else "n/a"}  '
          f'元素类型={type(p[0]).__name__ if isinstance(p, list) and p else "n/a"}')

# ---- ③ 嵌套复杂 schema（贴近真实行程结构） ----
print('=== ③ 嵌套行程 schema ===')
TRIP = {
    'type': 'object',
    'properties': {
        'days': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {
                'day': {'type': 'integer'},
                'theme': {'type': 'string'},
                'activities': {'type': 'array', 'items': {
                    'type': 'object',
                    'properties': {'name': {'type': 'string'}, 'note': {'type': 'string'}},
                    'required': ['name', 'note'], 'additionalProperties': False}},
            },
            'required': ['day', 'theme', 'activities'], 'additionalProperties': False}},
    },
    'required': ['days'], 'additionalProperties': False}

msgs = [
    {'role': 'system', 'content': '你是行程规划助手，只输出结构化数据。'},
    {'role': 'user', 'content':
        '为北京 2 天行程排期。候选景点：故宫、天坛、颐和园、长城、北海公园、南锣鼓巷。'
        '每天排 2 个，给出当天主题。只能使用候选里的景点名。'},
]
saved = MSGS[:]
MSGS[:] = msgs
content, err, dt = call({'response_format': {
    'type': 'json_schema', 'json_schema': {'name': 'trip', 'schema': TRIP}},
    'enable_thinking': True, 'max_tokens': 2000})
MSGS[:] = saved
if err:
    print('  ERR', err)
else:
    p = json.loads(content)
    print(f'  顶层={type(p).__name__} ({dt:.1f}s)')
    obj = p[0] if isinstance(p, list) and p else p
    print(f'  展开后 keys={list(obj) if isinstance(obj, dict) else "?"}')
    days = obj.get('days', []) if isinstance(obj, dict) else []
    print(f'  天数={len(days)}')
    for d in days:
        names = [a.get("name") for a in d.get("activities", [])]
        print(f'    day{d.get("day")} [{d.get("theme")}] -> {names}')
