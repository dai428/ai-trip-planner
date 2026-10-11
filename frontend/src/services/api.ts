/**
 * 接口封装层。
 *
 * 【为什么要单独一层】
 *    组件里不直接写 fetch —— 否则 URL 散落在十几个文件里，
 *    后端改个路径就要全局搜索替换。
 *    这里集中定义，组件只调 api.generate(...) 这样的语义化方法。
 */

import type {
  AmapStatus, CityMeta, CitySuggestion, PackingList, RagStats, RoutePlan, RouteRequest,
  ShareInfo, TripPlan, TripRequest, TripSummary,
} from '../types'

const TIMEOUT_MS = 30_000

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)

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
  /** 城市搜索：本地库 + 高德补全。q 走 URL 编码，中文不能直接拼进 query string */
  searchCities: (q: string, limit = 12) =>
    request<{ query: string; results: CitySuggestion[] }>(
      `/api/meta/cities/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),

  // ---- 行程 ----
  generate: (body: TripRequest) =>
    request<TripPlan>('/api/trip/generate', { method: 'POST', body: JSON.stringify(body) }),
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
}
