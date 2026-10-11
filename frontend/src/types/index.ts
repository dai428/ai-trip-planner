/**
 * 前端类型定义 —— 与后端 app/models 逐一对应。
 *
 * 【为什么要在前端再写一遍】
 *     TS 的好处就是"接口变了，编译期就报错"。
 *     Python 那边的 Pydantic 模型和这里的 interface 是一份契约的两端，
 *     改后端字段时同步改这里，就不会出现"页面读不到某个字段"的运行时惊喜。
 */

export interface CityMeta {
  name: string
  province: string
  tags: string[]
  best_season: string
  avg_days: number
  lng: number
  lat: number
}

export interface Activity {
  name: string
  kind: string
  note: string
  source: string | null
}

export interface DayPlan {
  day: number
  city: string
  theme: string
  activities: Activity[]
  meals: string[]
  lodging: string | null
}

export interface BudgetItem {
  label: string
  amount: number
}

/**
 * 一份行程的编排来源。
 *
 * 后端有两条生成路径，结果看上去都"像模像样"，用户无从分辨：
 *   llm  —— 大模型从候选池里选点并编排
 *   rule —— 规则生成（大模型未配置 / 超时 / 返回不合规时的兜底）
 * 所以后端把它显式放进契约，前端如实标注。降级不该被误读成"大模型就这水平"。
 */
export type Generator = 'llm' | 'rule'

export interface TripPlan {
  id: string
  cities: string[]
  days: number
  start_date: string | null
  pace: string
  itinerary: DayPlan[]
  budget: BudgetItem[]
  created_at: string
  generator: Generator
  generator_note: string | null
}

export interface PoiItem {
  name: string
  kind: string
  area: string
  address: string
  category: string
  tags: string[]
  origin: 'amap' | 'guide'
  source: string
}

export interface PoiPoolView {
  city: string
  count: number
  by_kind: Record<string, number>
  amap_enabled: boolean
  items: PoiItem[]
}

export interface LlmStatus {
  enabled: boolean
  mode: 'llm' | 'rule'
  model: string | null
  api_base: string | null
  thinking: boolean
  key_masked: string | null
  fallback: string | null
  note: string
}

export interface PoiPoolStats {
  enabled: boolean
  total: number
  ttl_days?: number
  cities: Record<string, Record<string, number>>
}

export interface PackingCategory {
  category: string
  items: string[]
}

export interface PackingList {
  trip_id: string
  cities: string[]
  days: number
  categories: PackingCategory[]
}

export interface TripSummary {
  id: string
  cities: string[]
  days: number
  created_at: string
}

export interface ShareInfo {
  token: string
  trip_id: string
  url: string
  created_at: string
}

export interface RagStats {
  cities: number
  documents: number
  by_kind: Record<string, number>
  channels: string[]
}

export interface TripRequest {
  cities: string[]
  days: number
  start_date?: string | null
  budget?: number | null
  preferences: string[]
  pace: string
}

export type Pace = '轻松' | '标准' | '紧凑'

// ==================== 城市搜索 ====================

/**
 * 一条城市搜索结果。
 *
 * has_guide 是这里最关键的字段：它告诉用户"这个城市有没有攻略库支撑"。
 * 有攻略 → 行程由本地语料检索生成，每条都带出处；
 * 没有   → 行程由高德 POI 兜底生成，内容会粗一些。
 * 把这个差别显式暴露在 UI 上，比让用户自己发现"怎么推荐得这么泛"要好。
 */
export interface CitySuggestion {
  name: string
  province: string | null
  tags: string[]
  has_guide: boolean
  avg_days: number | null
  source: 'library' | 'amap'
}

// ==================== 路线规划 ====================

export interface RouteLeg {
  from_city: string
  to_city: string
  distance_km: number
  duration_hours: number
  source: 'amap' | 'estimate'
}

export interface WaypointSuggestion {
  city: string
  detour_km: number
  progress: number
  attractions: number
  tags: string[]
  reason: string
}

export interface RoutePlan {
  origin: string
  destination: string
  origin_point: string | null
  destination_point: string | null
  days: number
  total_distance_km: number
  total_duration_hours: number
  source: 'amap' | 'estimate'
  needs_stop: boolean
  advice: string
  legs: RouteLeg[]
  waypoints: WaypointSuggestion[]
  suggested_cities: string[]
}

export interface RouteRequest {
  origin: string
  destination: string
  days: number
}

export interface AmapStatus {
  enabled: boolean
  mode: 'amap' | 'estimate'
  base_url: string | null
  cache_entries: number
  fallback: string | null
}
