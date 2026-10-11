"""全局配置。

【它在项目里的位置】
    配置层 —— 只放"会变的东西"：路径、开关、阈值。
    原则：代码里不写死魔法数字，一律从这里取。

【为什么用 Path(__file__) 而不是相对路径】
    相对路径依赖"你在哪个目录启动进程"，换个目录就崩。
    以本文件自身为锚点算出来的路径，永远指向项目内的固定位置。
"""

import os
from pathlib import Path


# ---------- 环境变量 ----------
def _load_dotenv() -> None:
    """极简 .env 读取器（不引第三方依赖）。

    规则：读 backend/.env 里的 KEY=VALUE，只在环境变量不存在时才写入。
    也就是"已导出的环境变量 > .env 文件"，方便临时覆盖调试。
    .env 已被 .gitignore 忽略，密钥不会进版本库。
    """
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# ---------- 路径锚点 ----------
BASE_DIR = Path(__file__).resolve().parent          # .../backend/app
DATA_DIR = BASE_DIR / "data"                        # .../backend/app/data
GUIDE_DIR = DATA_DIR / "guides"                     # 攻略库（RAG 的语料）
CITIES_FILE = DATA_DIR / "cities.json"              # 城市元数据
DB_DIR = BASE_DIR.parent / "db"                     # .../backend/db
DB_PATH = DB_DIR / "app.db"                         # SQLite 文件

# ---------- 应用元信息 ----------
APP_NAME = "行迹 · 智能行程规划"
APP_VERSION = "0.2.0"
APP_DESCRIPTION = "输入去哪儿、玩几天，得到一份带攻略出处、可拖拽调整、可导出日历的行程。"

# ---------- RAG 检索参数 ----------
# 说明：BM25（稀疏）是零依赖的底座，永远可用；
#      向量（稠密）与 Cross-encoder 重排需要额外模型，默认关闭，
#      环境具备时置 1 打开即可，代码会自动走"混合检索 + 重排"路径。
RAG_ENABLE_VECTOR = os.getenv("RAG_ENABLE_VECTOR", "0") == "1"
RAG_ENABLE_RERANK = os.getenv("RAG_ENABLE_RERANK", "0") == "1"

EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
RERANK_MODEL_NAME = os.getenv("RERANK_MODEL_NAME", "BAAI/bge-reranker-base")

CHUNK_SIZE = 400          # 文档块字符数
CHUNK_OVERLAP = 60        # 相邻块重叠字符数
RRF_K = 60                # RRF 融合常数（经验值，与检索规模无关）
BM25_TOP_K = 20           # 稀疏召回候选数
VECTOR_TOP_K = 20         # 稠密召回候选数
FUSION_TOP_K = 8          # RRF 融合后候选数
FINAL_TOP_K = 4           # 精排后最终返回条数

# ---------- 生成参数 ----------
MAX_CITIES = 6            # 单次最多串联城市数
MAX_DAYS = 30             # 单次最长天数
DAILY_ACTIVITY_BUDGET = {"轻松": 2, "标准": 3, "紧凑": 4}   # 每天安排几个景点

# ---------- 高德地图 ----------
# 配了 Key 走真实高德；没配自动降级为本地经纬度估算（球面距离 + 路网系数）。
# 这样"功能永远是完整的"，只是精度不同 —— 配置改变行为，代码不用改。
AMAP_API_KEY = os.getenv("AMAP_API_KEY", "").strip()
AMAP_BASE_URL = os.getenv("AMAP_BASE_URL", "https://restapi.amap.com/v3").rstrip("/")
AMAP_TIMEOUT = float(os.getenv("AMAP_TIMEOUT_SECONDS", "10"))
AMAP_CACHE_TTL = int(os.getenv("AMAP_CACHE_TTL_SECONDS", "600"))   # 秒

# 无 Key 时的估算参数
ROAD_FACTOR = 1.28        # 实际公路里程约为直线距离的倍数
AVG_SPEED_KMH = 75.0      # 长途自驾平均时速（含服务区休息）

# 可接受的"地点"粒度（对应高德地理编码返回的 level 字段）
# 【为什么要卡这个】
#   高德对随意输入的匹配极其宽松，会尽量"猜"一个答案给你：
#     输入「不存在的城市xyz」→ 它把「城市」当成关键词，
#     依次匹配到「银川某村庄」和「香港东区某个叫'城市'的兴趣点」。
#   不拦截的后果不是报错，而是**静默生成一条从银川/香港出发的荒谬路线**，
#   用户还以为是自己的输入被正确识别了。静默的错误远比明确的报错糟糕。
#
#   实测所有真实城市都落在「市」或「区县」上（成都=市、香格里拉=区县、
#   大理=区县、张家界=市），所以只认行政区划这三档：
#       省 / 市 / 区县
#   村庄、门牌号、道路、地铁站，以及"兴趣点"（景区、车站等具名地标）
#   一律拒收 —— 出发地/目的地本来就该填城市，而不是某个景点门口。
AMAP_ACCEPT_LEVELS = {"省", "市", "区县"}

# ---------- 高德 POI 候选池 ----------
# 【它解决什么】
#   把"某城市有什么可玩的"提前攒成一张本地表，供大模型从中选点。
#   否则每次生成行程都要临时外呼几十次，既慢又费配额，候选还少得可怜。
#
# 【30 天 TTL 是怎么定的】
#   POI 的存续以月计：一家店开张/关门不是日更的事情。
#   定太短（如 1 天）等于每次都用新鲜度换延迟和配额，不划算；
#   定太长（如 1 年）会积累已停业的点。30 天是"够新且省"的折中。
POI_POOL_ENABLED = os.getenv("POI_POOL_ENABLED", "1") == "1"
POI_POOL_TTL_DAYS = int(os.getenv("POI_POOL_TTL_DAYS", "30"))
POI_POOL_MAX_PER_CITY = int(os.getenv("POI_POOL_MAX_PER_CITY", "120"))
POI_POOL_PAGE_SIZE = 25          # 高德单页上限就是 25，别调大

# ---------- 大模型（可选增强）----------
# 与高德同样的哲学：配了就启用，没配或调用失败自动降级为纯规则生成。
# 走 OpenAI 兼容协议，因此换厂商只改这三行配置，代码不动。
#
# 【为什么键名是 LLM_API_KEY 而不是 DASHSCOPE_API_KEY】
#     _load_dotenv 用的是 os.environ.setdefault —— 已存在的环境变量优先。
#     本机环境里恰好已有一个同名但失效的 DASHSCOPE_API_KEY，
#     若沿用该名字，.env 里的真实 Key 会被静默忽略，排查起来极其费时。
#     取一个不冲突的名字，从根上避免这个坑。
LLM_ENABLED = os.getenv("LLM_ENABLED", "1") == "1"
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_API_BASE = os.getenv(
    "LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
).rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.8-27b")
# 超时给得比较宽：实测这个模型生成一份 5 天行程需要 30~40 秒，
# 关掉思考模式前更是要 117 秒。定 60 秒会让正常请求被误判为失败而白白降级。
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.4"))

# 思考模式。默认关闭：实测它**不影响**结构化输出的顶层形状
# （开与关都返回单元素数组，见 llm_service 模块头部的实测契约），
# 却会额外产生数百个 reasoning token、把耗时从几秒推到二十秒。
# 想要更强的排期质量时可以打开，代价是响应变慢。
LLM_ENABLE_THINKING = os.getenv("LLM_ENABLE_THINKING", "0") == "1"

# 送进提示词的候选**安全上限**（不是目标值）。
# 【为什么需要它，以及为什么不是固定值】
#   真正的额度由 trip_service 按"这个城市要玩几天、节奏多紧"算出来，
#   让候选数随行程规模增长。这里只是防止极端输入（如 6 城 30 天）
#   把提示词撑爆上下文 —— 它是兜底的天花板，不是默认用量。
#   池子存宽（供筛选、供兜底），提示词喂窄（省 token、降干扰）。
LLM_PROMPT_CANDIDATES = int(os.getenv("LLM_PROMPT_CANDIDATES", "40"))
LLM_PROMPT_CANDIDATES_FOOD = int(os.getenv("LLM_PROMPT_CANDIDATES_FOOD", "20"))
LLM_PROMPT_CANDIDATES_LODGING = int(os.getenv("LLM_PROMPT_CANDIDATES_LODGING", "8"))

# ---------- 路线与中途推荐 ----------
STOP_DISTANCE_KM = 500.0     # 超过这个里程，建议中途停留
STOP_DURATION_H = 6.0        # 超过这个驾车时长，建议中途停留
CORRIDOR_MAX_KM = 130.0      # 候选城市到路线的最大垂直距离（超出算"不顺路"）
CORRIDOR_MIN_PROGRESS = 0.15 # 投影位置：路程 15%~85% 之间才算"中途"
CORRIDOR_MAX_PROGRESS = 0.85
MAX_WAYPOINTS = 2            # 最多推荐几个中途城市
DETOUR_MAX_KM = 260.0        # 绕路超过这个数就不值得推荐
