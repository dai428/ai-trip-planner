/**
 * 接口封装层。
 *
 * 【为什么要单独一层】
 *    组件里不直接写 fetch —— 否则 URL 散落在十几个文件里，
 *    后端改个路径就要全局搜索替换。
 *    这里集中定义，组件只调 api.generate(...) 这样的语义化方法。
 */

import type {
  AmapStatus, CityMeta, CitySuggestion, LlmStatus, PackingList, PoiPoolStats, PoiPoolView,
  RagStats, RoutePlan, RouteRequest, ShareInfo, TripPlan, TripRequest, TripSummary,
} from '../types'

const TIMEOUT_MS = 30_000

/**
 * 生成行程的超时单独放宽。
 *
 * 【为什么不跟其他接口共用一个超时】
 *   绝大多数接口是毫秒级的读写，30 秒绰绰有余。
 *   但 /trip/generate 要真实调用大模型，实测一份 5 天行程约 17~25 秒，
 *   城市更多、天数更长时会线性增长 —— 用 30 秒去卡它，
 *   用户看到的是"请求超时"，而后端其实还在正常跑并最终会成功。
 *
 * 【为什么是 240 秒，而不是随便一个大数】
 *   它必须**严格大于**后端自己的 LLM 超时（LLM_TIMEOUT_SECONDS，默认 180 秒）。
 *   后端超时后会自己降级到规则生成并正常返回，那是一次成功响应；
 *   如果前端先一步放弃，用户就永远看不到那份降级结果 ——
 *   把"后端能兜住"变成"前端直接失败"，白白浪费了兜底设计。
 *   留 60 秒余量覆盖候选池构建与序列化开销。
 */
const GENERATE_TIMEOUT_MS = 240_000

async function request<T>(path: string, init?: RequestInit, timeoutMs = TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const resp = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      ...init,
    })
    if (!resp.ok) {
      // 后端用 {detail: "..."} 报错，尽量把它翻出来给人看
      let detail = `请求失败（${resp.status}）`
      try {
        const body = await resp.json()
        if (body?.detail) {
          detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
        }
      } catch { /* 响应体不是 JSON，用默认文案 */ }
      throw new Error(detail)
    }
    if (resp.status === 204) return undefined as T
    return (await resp.json()) as T
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new Error('请求超时，请检查后端服务是否已启动')
    }
    throw e
  } finally {
    clearTimeout(timer)
  }
}

export const api = {
  // ---- 元信息 ----
  cities: () => request<{ cities: CityMeta[] }>('/api/meta/cities'),
  preferences: () => request<{ preferences: string[] }>('/api/meta/preferences'),
  ragStats: () => request<RagStats>('/api/meta/rag'),
  amapStatus: () => request<AmapStatus>('/api/meta/amap'),
  llmStatus: () => request<LlmStatus>('/api/meta/llm'),
  poiStats: () => request<PoiPoolStats>('/api/meta/poi'),
  /** 某城市的候选池内容（会按需触发一次高德召回，所以超时放宽） */
  poiPool: (city: string, kind?: string, limit = 60) =>
    request<PoiPoolView>(
      `/api/poi/${encodeURIComponent(city)}?limit=${limit}${kind ? `&kind=${encodeURIComponent(kind)}` : ''}`,
      undefined,
      60_000,
    ),
  /** 城市搜索：本地库 + 高德补全。q 走 URL 编码，中文不能直接拼进 query string */
  searchCities: (q: string, limit = 12) =>
    request<{ query: string; results: CitySuggestion[] }>(
      `/api/meta/cities/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),

  // ---- 行程 ----
  generate: (body: TripRequest) =>
    request<TripPlan>(
      '/api/trip/generate',
      { method: 'POST', body: JSON.stringify(body) },
      GENERATE_TIMEOUT_MS,
    ),
  getTrip: (id: string) => request<TripPlan>(`/api/trip/${id}`),
  history: () => request<TripSummary[]>('/api/trip'),
  removeTrip: (id: string) => request<{ message: string }>(`/api/trip/${id}`, { method: 'DELETE' }),

  // ---- 路线 ----
  planRoute: (body: RouteRequest) =>
    request<RoutePlan>('/api/trip/route', { method: 'POST', body: JSON.stringify(body) }),

  // ---- 新功能 ----
  packingList: (id: string) => request<PackingList>(`/api/trip/${id}/packing-list`),
  createShare: (id: string) => request<ShareInfo>(`/api/trip/${id}/share`, { method: 'POST' }),
  getShared: (token: string) => request<TripPlan>(`/api/share/${token}`),

  /** ICS 是文件下载，直接给浏览器地址，不走 fetch */
  icsUrl: (id: string) => `/api/trip/${id}/calendar.ics`,

  /**
   * Word / PDF 行程单同样是文件下载，同样直接给地址。
   *
   * 【为什么不 fetch 再转 blob】
   *   走 fetch 要自己处理文件名（Content-Disposition 里的 filename* 是 RFC 5987
   *   编码的，解析它得再写一遍 percent-decode），而 `<a download>` 会让浏览器
   *   直接采用响应头里的文件名 —— 中文名也不会乱码。
   *   少一次全量数据过 JS 内存，几十 KB 的文档直接落盘。
   */
  exportDocxUrl: (id: string) => `/api/trip/${id}/export.docx`,
  exportPdfUrl: (id: string) => `/api/trip/${id}/export.pdf`,
}
