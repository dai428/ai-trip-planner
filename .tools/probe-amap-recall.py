"""探针：确认高德 /place/text 的分类召回能力。

要回答的问题：按分类编码 + 关键词翻页，每个城市到底能召回多少去重后的 POI？
如果拼死只召回 20 条，那"60~100 条候选池"就是拍脑袋的指标，得改设计。
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.services import amap_service as A  # noqa: E402

print(f'高德启用: {A.enabled()}   模式: {A.mode()}')
print('=' * 72)

# (kind, 关键词, 分类编码)
PROBES = [
    ('景点', '景点', '110000'),
    ('景点', '博物馆', '140000'),
    ('美食', '美食', '050000'),
    ('住宿', '酒店', '100000'),
]

for city in ['北京', '大理']:
    print(f'\n### {city}')
    total = set()
    for kind, kw, code in PROBES:
        got = []
        for page in range(1, 4):
            data = A._get('/place/text', keywords=kw, city=city, types=code,
                          citylimit='true', offset=25, page=page, extensions='base')
            pois = (data or {}).get('pois') or []
            if not pois:
                break
            got.extend(pois)
        names = []
        for p in got:
            n = p.get('name')
            if n:
                names.append(n)
        # 去重（同一分类内）
        uniq_cat = list(dict.fromkeys(names))
        total.update(uniq_cat)
        t = (got[0].get('type') if got else '') or ''
        print(f'  {kind:4s} {kw:4s} {code}  page1-3 原始 {len(got):3d} 条 '
              f'→ 去重 {len(uniq_cat):3d} 条   样例type={t[:34]}')
        print(f'       样例: {uniq_cat[:4]}')
    print(f'  >>> {city} 跨分类合计去重: {len(total)} 条')
