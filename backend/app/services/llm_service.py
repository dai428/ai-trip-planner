"""大模型服务：让模型从候选池里选点并编排行程。

【设计立场：这是可选增强，不是硬依赖】
    跟高德一样：配了 Key 就走大模型，没配 / 超时 / 返回不合规，
    **静默退回规则生成**，功能永远是完整的。
    调用方通过返回值里的 source 字段知道"这份行程是怎么来的"。

【为什么用 urllib 手写 HTTP，不引 openai SDK】
    本项目的约束是"零部署一键起"：requirements 里每多一个包，
    就多一分安装失败的概率。这里只用到 /chat/completions 一个接口，
    协议是固定的 JSON over HTTP —— 手写不到 40 行，
    比多一个依赖划算。真要换 SDK，改 _chat 一个函数即可。

============================================================
【实测得到的结构化输出契约 —— 这段是踩过坑才写下的，别凭直觉改】
============================================================
    在百炼的 OpenAI 兼容端点上，用 response_format=json_schema 时：

    ① **顶层声明 type=object 是无效的。**
       连续 5 次调用，返回的顶层**永远是长度为 1 的数组** [{...}]，
       把声明的对象原样包了一层。不是偶发，是稳定行为。

    ② **把 schema 顶层改成 type=array 也不能让它"合规"。**
       依然返回 [{...}]，元素数恒为 1。

    ③ **但嵌套结构是被严格遵守的。**
       内层 days[].activities[].name 的字段名、类型、枚举约束都命中了，
       说明约束解码只作用在**内层**，顶层被端点特殊处理了。

    ④ response_format=json_object 直接 400，要求 messages 里出现 "json" 字样。

    因此本模块的做法是：**schema 照常声明成想要的形状（自文档化），
    解析时统一做一次"拆壳"归一化**。这比为了迎合缺陷把 schema 写成数组更
    可读 —— 而且万一哪天端点修好了，归一化对已经是对象的结果也完全无害。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from app import config

logger = logging.getLogger(__name__)

# ==================== 提示词与 Schema ====================

_SYSTEM = """你是一个专业的行程规划师。你的任务是从给定的候选地点中挑选，并编排成逐日行程。

硬性规则（违反即视为无效输出）：
1. 只能使用【候选列表】里出现过的地点名，必须**原样照抄**，不得改写、缩写、加后缀或自行发明。
2. 每天安排的景点数量必须等于【每天景点数】。候选不足时可以少排，但绝不可以用列表外的地点凑数。
3. 每个景点配一句 note（**20 字以内**），说明为什么把它排在这一天（结合用户的偏好与该地点自身特点），不要写"值得一去"这类空话。
   注：输出长度直接决定响应速度，务必简短。
4. meals 只能从该城市的【美食】候选里选，最多 2 条。
5. lodging 只能从该城市的【住宿】候选里选 1 条；没有候选就填空字符串。
6. theme 是一天的主题短语（8 字以内），要能概括当天的内容，不同天的主题不要重复。
7. day 字段是全局序号，从 1 开始连续递增，跨城市不重置。
8. 只输出 JSON，不要输出任何解释、markdown 代码块或前后缀文字。"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "days": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "day": {"type": "integer"},
                    "city": {"type": "string"},
                    "theme": {"type": "string"},
                    "activities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "note": {"type": "string"},
                            },
                            "required": ["name", "note"],
                            "additionalProperties": False,
                        },
                    },
                    "meals": {"type": "array", "items": {"type": "string"}},
                    "lodging": {"type": "string"},
                },
                "required": ["day", "city", "theme", "activities", "meals", "lodging"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["days"],
    "additionalProperties": False,
}


# ==================== 传输层 ====================

def enabled() -> bool:
    return bool(config.LLM_ENABLED and config.LLM_API_KEY)


def mode() -> str:
    return "llm" if enabled() else "rule"


def _chat(messages: list[dict], *, schema: dict | None = None,
          max_tokens: int | None = None, temperature: float | None = None) -> str | None:
    """打一次 /chat/completions，返回 assistant 的正文；失败返回 None。

    失败一律吞掉返回 None —— 上层据此走降级分支。
    把异常抛出去会让"大模型挂了"变成"整个行程生成 500"，
    这与"可选增强"的定位相矛盾。
    """
    if not enabled():
        return None

    body: dict = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": config.LLM_TEMPERATURE if temperature is None else temperature,
        "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
        # 【必须显式传 False，不能只在 True 时才带上这个字段】
        #   qwen3 系列**默认开启思考模式**。实测同一请求：
        #       不传该字段   → completion_tokens=6015，耗时 117s
        #       显式传 False → completion_tokens 骤降，耗时约 1/4
        #   也就是说"不传"并不等于"关闭"，省下这个字段反而会付出
        #   四倍延迟与四倍 token 的代价。这类默认值陷阱只能靠实测发现。
        "enable_thinking": config.LLM_ENABLE_THINKING,
    }
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "itinerary", "schema": schema},
        }

    req = urllib.request.Request(
        f"{config.LLM_API_BASE}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    # 只重试一次，且只在"网络层面"失败时 —— 
    # 4xx 是我们自己的请求有问题，重试一百次也一样，只会白白拖长用户等待。
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return (data["choices"][0]["message"].get("content") or "").strip() or None
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()[:200]
            except Exception:
                pass
            logger.warning(f"HTTP {exc.code}: {detail}")
            return None          # 4xx/5xx 不重试
        except Exception as exc:
            logger.warning(f"{type(exc).__name__}: {exc} (第 {attempt} 次)")
            if attempt == 1:
                time.sleep(1.0)
    return None


def _unwrap(obj):
    """结构化输出的"拆壳"归一化。

    【为什么需要它】
        见模块头部实测契约 ①②：这个端点会把声明的对象包进一层单元素数组。
        这里统一拆掉，让调用方永远拿到对象。
        对"本来就是对象"的返回完全无害 —— 所以无论端点行为如何都安全。
    """
    if isinstance(obj, list):
        dicts = [x for x in obj if isinstance(x, dict)]
        return dicts[0] if dicts else None
    return obj if isinstance(obj, dict) else None


def complete_json(system: str, user: str, schema: dict) -> dict | None:
    """要一段符合 schema 的 JSON，返回**对象**（已拆壳）；拿不到返回 None。"""
    raw = _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        schema=schema,
    )
    if not raw:
        return None

    # 模型偶尔仍会裹一层 ```json 代码块，尽管提示词明确禁止。剥掉再解析。
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("返回不是合法 JSON")
        return None

    obj = _unwrap(parsed)
    if obj is None:
        logger.warning("拆壳后仍不是对象")
    return obj


# ==================== 领域层：编排行程 ====================

def _candidate_block(city: str, pool: dict[str, list[dict]]) -> dict:
    """把该城市的候选压成提示词里的一段。

    【为什么用对象数组，而不是「名字｜区域｜标签」这种拼串】
        第一版就是拼串。实测模型会把**整行**当成人名抄回来：
            返回的 name = "故宫博物院｜东城区｜历史/必去"
        于是逐一核对候选池时一条都对不上，合规率 0%，整份行程被作废。
        原因很清楚：分隔符对模型来说不是"字段边界"，而只是字符串的一部分。
        改成分离的字段（name / area / tags），边界就不再需要模型去猜。

    只发名字、区域、标签 —— **不发地址、坐标、出处**，
    这些字段对"选哪个、怎么排"没有帮助，却会显著增加 token。
    """
    def brief(items: list[dict], cap: int) -> list[dict]:
        return [
            {
                "name": it["name"],
                "area": it.get("area") or "",
                "tags": list(it.get("tags", []))[:2],
            }
            for it in items[:cap]
        ]

    return {
        "城市": city,
        "景点候选": brief(pool.get("景点", []), config.LLM_PROMPT_CANDIDATES),
        "美食候选": brief(pool.get("美食", []), config.LLM_PROMPT_CANDIDATES_FOOD),
        "住宿候选": brief(pool.get("住宿", []), config.LLM_PROMPT_CANDIDATES_LODGING),
    }


def _build_user_prompt(ctx: dict) -> str:
    """ctx: cities / allocation / preferences / pace / per_day / pool_by_city"""
    plan = [
        {"城市": c, "安排天数": ctx["allocation"][c]}
        for c in ctx["cities"]
    ]
    payload = {
        "用户偏好": ctx["preferences"] or ["无特别偏好"],
        "节奏": ctx["pace"],
        "每天景点数": ctx["per_day"],
        "总天数": sum(ctx["allocation"].values()),
        "行程分段": plan,
        "候选列表": [_candidate_block(c, ctx["pool_by_city"][c]) for c in ctx["cities"]],
    }
    return (
        "请按下面的信息编排行程。\n"
        "注意：景点/美食/住宿的名字必须从对应城市的候选列表中**原样照抄**。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def compose_days(ctx: dict) -> tuple[list[dict] | None, dict]:
    """让大模型排行程。返回 (days, meta)。

    days 为 None 表示"这次没用上"，原因写在 meta['reason'] 里。
    【为什么要把失败原因带出来】
        静默降级最难排查：用户看到行程"好像还行"，其实规则生成的结果，
        没人知道大模型根本没参与。带上原因，/api/meta/llm 和生成接口
        都能把它透出来，问题一眼可见。
    """
    meta: dict = {"used": False, "reason": None, "elapsed": 0.0}

    if not enabled():
        meta["reason"] = "未配置大模型"
        return None, meta

    t0 = time.time()
    # 输出量随天数增长；思考模式下 reasoning tokens 也吃这个预算，所以给宽一点。
    est = min(8192, 600 + 280 * sum(ctx["allocation"].values()))
    obj = complete_json(_SYSTEM, _build_user_prompt(ctx), _SCHEMA)
    meta["elapsed"] = round(time.time() - t0, 2)

    if not obj:
        meta["reason"] = "模型未返回可用结果"
        return None, meta

    days = _validate(obj.get("days"), ctx, meta)
    if days is None:
        return None, meta

    meta["used"] = True
    meta["reason"] = "大模型编排"
    return days, meta


def _clean_name(raw) -> str:
    """把模型返回的名字收拾干净，再拿去跟候选池比对。

    【为什么不能直接信任】
        模型的"原样照抄"是按它的理解来的。实测它会把整行拆解成
        「故宫博物院｜东城区｜历史/必去」当成一个名字还回来；
        也会顺手加上《》、「」、前后空格，或把全角括号换成半角。
        这些都是**无害的噪声**，不该因此判整份行程作废。
        这里只做"去装饰"级别的修正，绝不猜名字 ——
        猜出来的东西一旦进了池子比对，防幻觉就形同虚设。
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    # 取第一个分隔符之前的部分（分隔符可能被模型半角化）
    for sep in ("｜", "|", "\t"):
        if sep in s:
            s = s.split(sep)[0]
    # 去掉书名号/引号等包装
    return s.strip().strip("《》「」『』\"'“”‘’ ").strip()


def _validate(days, ctx: dict, meta: dict) -> list[dict] | None:
    """把模型输出对齐到候选池上 —— 这是防幻觉的唯一有效手段。

    【为什么不能只靠提示词】
        "只能用候选里的名字"写在提示词里只是**强烈建议**，
        模型仍然会写出「故宫博物院」而候选里是「故宫」，
        或者凭空补一个「南锣鼓巷」而候选里根本没有。
        所以必须拿结果回去逐个核对：**不在池子里的，一律丢掉**。

    【丢弃比例过高就整体放弃】
        如果模型大部分名字都是编的，说明这次调用不可信；
        与其交出一份七零八落的行程，不如整个退回规则生成 ——
        至少那份是完整可用的。
    """
    candidates: dict[str, dict[str, set[str]]] = {}
    for city in ctx["cities"]:
        pool = ctx["pool_by_city"][city]
        candidates[city] = {
            k: {it["name"] for it in pool.get(k, [])} for k in ("景点", "美食", "住宿")
        }
    # 全量合法名字（模型偶尔会把景点归到别的城市，只要池子里有就认）
    all_names: set[str] = set()
    for by_kind in candidates.values():
        for names in by_kind.values():
            all_names |= names

    if not isinstance(days, list) or not days:
        meta["reason"] = "模型返回的 days 不是非空数组"
        return None

    # 【两个指标测的是两件不同的事，别混为一谈】
    #   valid_ratio        —— 行程被合法内容填满的程度。**决定要不要采用这次结果**
    #                         它是"够不够用"，不是"模型干不干净"。
    #   hallucination_rate —— 模型编造的比例。**只用于可观测性**，不参与判定。
    #
    # 为什么不拿编造率去否决：模型多写了 20 个虚构地名、但把真实该有的 4 个都写对了，
    # 用户拿到的仍是一份正确的行程 —— 虚构的那些已经被逐条丢掉了。
    # 拿编造率否决反而会误伤，但它值得被记录下来，
    # 因为"编造率突然升高"往往是提示词或模型版本变化的早期信号。
    expected = ctx["per_day"] * sum(ctx["allocation"].values())
    valid_slots = 0
    dropped = 0
    cleaned: list[dict] = []

    for d in days:
        if not isinstance(d, dict):
            continue
        city = d.get("city")
        if city not in candidates:
            city = ctx["cities"][0]

        acts = []
        for a in (d.get("activities") or []):
            if not isinstance(a, dict):
                continue
            name = _clean_name(a.get("name"))
            # 优先认"本城市的候选"；名字合法但归错城市也接受 ——
            # 城市归属本来就由我们按分段覆盖，不必因此丢掉一条好推荐。
            if name in candidates[city]["景点"] or name in candidates[city]["美食"]:
                acts.append({"name": name, "note": (a.get("note") or "").strip()})
                valid_slots += 1
            elif name in all_names:
                acts.append({"name": name, "note": (a.get("note") or "").strip()})
                valid_slots += 1
            else:
                dropped += 1

        meals = []
        for m in (d.get("meals") or []):
            cm = _clean_name(m)
            if cm in candidates[city]["美食"] and cm not in meals:
                meals.append(cm)
            elif cm:
                dropped += 1

        lodging = _clean_name(d.get("lodging"))
        if lodging and lodging not in candidates[city]["住宿"]:
            # 高德返回的酒店名常带分店后缀，攻略返回的是区域名 ——
            # 精确匹配不上就退回"包含"匹配，仍然限定在候选池内。
            lodging = next((x for x in candidates[city]["住宿"]
                            if lodging in x or x in lodging), "")
            if not lodging:
                dropped += 1

        cleaned.append({
            "day": d.get("day"),
            "city": city,
            "theme": (d.get("theme") or "").strip(),
            "activities": acts,
            "meals": meals[:2],
            "lodging": lodging or None,
        })

    if not cleaned:
        meta["reason"] = "模型输出的天数无法解析"
        return None

    ratio = valid_slots / expected if expected else 0
    meta["valid_ratio"] = round(ratio, 2)
    meta["dropped"] = dropped
    total_named = valid_slots + dropped
    meta["hallucination_rate"] = round(dropped / total_named, 2) if total_named else 0.0
    if ratio < 0.6:
        meta["reason"] = f"合规率过低（{ratio:.0%}），改用规则生成"
        return None

    return cleaned


def status() -> dict:
    """给 /api/meta/llm 用的自检信息（不含密钥）。"""
    return {
        "enabled": enabled(),
        "mode": mode(),
        "model": config.LLM_MODEL if enabled() else None,
        "api_base": config.LLM_API_BASE if enabled() else None,
        "thinking": config.LLM_ENABLE_THINKING,
        "key_masked": (config.LLM_API_KEY[:8] + "…" + config.LLM_API_KEY[-4:])
        if config.LLM_API_KEY else None,
        "fallback": None if enabled() else "规则生成（RAG 检索 + 高德候选池）",
        "note": "结构化输出顶层会被端点包一层数组，已在 _unwrap 中归一化",
    }
