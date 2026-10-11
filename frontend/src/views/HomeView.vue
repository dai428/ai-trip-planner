<script setup lang="ts">
/**
 * 首页：两种入口 —— 「选城市」和「规划路线」。
 *
 * 【为什么要分两个入口】
 *    用户脑子里有两种截然不同的问题：
 *      A. "我想去成都和重庆，玩 5 天"       → 直接选城市
 *      B. "我要从北京开车去大理，怎么走"     → 先说起点终点，再让系统回答中间停哪儿
 *    硬塞进一个表单会让两边都别扭。所以拆成两个模式，共用同一套参数（偏好/节奏/日期）。
 *
 * 【采纳路线后为什么不回填表单、而是直接生成】
 *    用户点"按这条路线生成行程"时，意图已经很明确了：
 *    再让他回去看一眼表单、再点一次"生成"，纯属多余。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import CityPicker from '../components/CityPicker.vue'
import RoutePlanner from '../components/RoutePlanner.vue'
import { api } from '../services/api'
import type { AmapStatus, CityMeta, Pace, RagStats } from '../types'

const router = useRouter()

const cities = ref<CityMeta[]>([])
const preferences = ref<string[]>([])
const rag = ref<RagStats | null>(null)
const amap = ref<AmapStatus | null>(null)
const loadError = ref('')

const mode = ref<'cities' | 'route'>('cities')
const picked = ref<string[]>([])
const days = ref(5)
const startDate = ref(toDateInput(new Date()))
const pace = ref<Pace>('标准')
const prefs = ref<string[]>([])

const submitting = ref(false)
const error = ref('')

const PACES: { value: Pace; hint: string }[] = [
  { value: '轻松', hint: '每天 2 个景点' },
  { value: '标准', hint: '每天 3 个景点' },
  { value: '紧凑', hint: '每天 4 个景点' },
]

/** 根据所选城市的建议天数算一个推荐值（仅攻略库内的城市有建议天数） */
const suggestedDays = computed(() => {
  if (!picked.value.length) return 0
  const sum = picked.value
    .map((n) => cities.value.find((c) => c.name === n)?.avg_days ?? 3)
    .reduce((a, b) => a + b, 0)
  return Math.min(Math.max(sum, 1), 30)
})

// 选城市时如果用户还没动过天数，就自动带上建议值
let daysTouched = false
watch(days, () => { daysTouched = true })
watch(picked, () => {
  if (!daysTouched && suggestedDays.value) days.value = suggestedDays.value
})

function toDateInput(d: Date) {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function togglePref(tag: string) {
  const i = prefs.value.indexOf(tag)
  if (i >= 0) prefs.value.splice(i, 1)
  else prefs.value.push(tag)
}

async function submit() {
  error.value = ''
  if (!picked.value.length) {
    error.value = '请至少选择一个目的地城市'
    return
  }
  submitting.value = true
  try {
    const plan = await api.generate({
      cities: picked.value,
      days: days.value,
      start_date: startDate.value || null,
      preferences: prefs.value,
      pace: pace.value,
    })
    router.push({ name: 'trip', params: { id: plan.id } })
  } catch (e) {
    error.value = e instanceof Error ? e.message : '生成失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

/** 路线模式：采纳推荐城市后直接生成，省掉一次多余的确认 */
function onAdoptRoute(adopted: string[], routeDays: number) {
  picked.value = adopted
  days.value = routeDays
  daysTouched = true
  submit()
}

onMounted(async () => {
  try {
    const [c, p, r, a] = await Promise.all([
      api.cities(), api.preferences(), api.ragStats(), api.amapStatus(),
    ])
    cities.value = c.cities
    preferences.value = p.preferences
    rag.value = r
    amap.value = a
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '初始化失败，请确认后端服务已启动'
  }
})
</script>

<template>
  <div class="stack" style="gap: 26px">
    <!-- 首屏标题 -->
    <section class="stack fade-in" style="gap: 12px">
      <h1 style="font-size: 34px; letter-spacing: -0.03em">
        去哪儿、玩几天，<span style="color: var(--primary)">剩下的交给行迹</span>
      </h1>
      <p style="color: var(--text-dim); max-width: 620px">
        直接选城市，或者告诉它从哪儿开到哪儿 —— 系统会判断中途值不值得停、停哪儿，
        再从攻略库检索素材生成逐日行程。每条安排都标注出处，可拖拽调整、可导出日历、可一键分享。
      </p>
      <div class="row wrap" style="gap: 8px">
        <span v-if="rag" class="chip chip-primary">{{ rag.cities }} 座城市有完整攻略</span>
        <span v-if="rag" class="chip">{{ rag.documents }} 条攻略语料</span>
        <span v-if="rag" class="chip" :title="rag.channels.join(' + ')">
          检索链路 · {{ rag.channels.length }} 路召回
        </span>
        <span
          v-if="amap"
          class="chip"
          :class="amap.enabled ? 'chip-accent' : ''"
          :title="amap.enabled
            ? '里程与时长来自高德实时路径规划，城市搜索覆盖全国'
            : '未配置高德 Key：里程按直线距离 × 路网系数估算，城市搜索仅覆盖已收录城市'"
        >{{ amap.enabled ? '高德已接入' : '本地估算模式' }}</span>
      </div>
    </section>

    <div v-if="loadError" class="alert">{{ loadError }}</div>

    <!-- 表单 -->
    <section class="card card-pad stack" style="gap: 24px">
      <!-- 模式切换 -->
      <div class="row wrap" style="gap: 12px">
        <div class="mode-tabs" role="tablist">
          <button
            class="mode-tab"
            role="tab"
            :aria-selected="mode === 'cities'"
            @click="mode = 'cities'"
          >选城市</button>
          <button
            class="mode-tab"
            role="tab"
            :aria-selected="mode === 'route'"
            @click="mode = 'route'"
          >规划路线</button>
        </div>
        <span class="section-hint">
          {{ mode === 'cities' ? '先决定去哪几座城市' : '先说清起点终点，系统再回答中间停哪儿' }}
        </span>
      </div>

      <div class="stack" style="gap: 12px">
        <div class="section-title">1 · {{ mode === 'cities' ? '选择目的地' : '出发地 → 目的地' }}</div>
        <CityPicker v-if="mode === 'cities'" v-model="picked" :cities="cities" :max="6" />
        <RoutePlanner v-else @adopt="onAdoptRoute" />
      </div>

      <div class="stack" style="gap: 12px">
        <div class="section-title">2 · 行程参数</div>
        <div class="row wrap" style="gap: 18px; align-items: flex-start">
          <div v-if="mode === 'cities'" class="field" style="flex: 1; min-width: 220px">
            <label class="field-label">
              总天数
              <span v-if="suggestedDays" class="section-hint">
                · 建议 {{ suggestedDays }} 天
                <button
                  class="btn btn-ghost btn-sm"
                  style="padding: 0 4px; color: var(--primary)"
                  @click="days = suggestedDays; daysTouched = true"
                >采用</button>
              </span>
            </label>
            <div class="row" style="gap: 12px">
              <input v-model.number="days" type="range" min="1" max="30" style="flex: 1; accent-color: var(--primary)" />
              <span style="min-width: 52px; font-weight: 640; font-variant-numeric: tabular-nums">{{ days }} 天</span>
            </div>
          </div>

          <div class="field" style="min-width: 190px">
            <label class="field-label">出发日期</label>
            <input v-model="startDate" type="date" class="input" />
          </div>
        </div>

        <div class="field">
          <label class="field-label">节奏</label>
          <div class="row wrap" style="gap: 9px">
            <button
              v-for="p in PACES"
              :key="p.value"
              class="tag-select"
              :aria-pressed="pace === p.value"
              :title="p.hint"
              @click="pace = p.value"
            >{{ p.value }}</button>
          </div>
        </div>

        <div class="field">
          <label class="field-label">偏好（可多选，影响推荐顺序）</label>
          <div class="row wrap" style="gap: 9px">
            <button
              v-for="tag in preferences"
              :key="tag"
              class="tag-select"
              :aria-pressed="prefs.includes(tag)"
              @click="togglePref(tag)"
            >{{ tag }}</button>
          </div>
        </div>
      </div>

      <div v-if="error" class="alert">{{ error }}</div>

      <!-- 路线模式下，"生成"由 RoutePlanner 的采纳按钮触发，这里不再重复一个按钮 -->
      <div v-if="mode === 'cities'" class="stack" style="gap: 8px">
        <div class="row-between wrap" style="gap: 12px">
          <span class="section-hint">
            {{ picked.length ? `将生成 ${picked.join(' → ')} · ${days} 天的行程` : '请先选择目的地' }}
          </span>
          <button class="btn btn-primary btn-lg" :disabled="submitting || !picked.length" @click="submit">
            <span v-if="submitting" class="spinner" />
            {{ submitting ? '正在生成…' : '生成行程' }}
          </button>
        </div>
        <!-- 说明为什么慢：生成要经过"高德召回候选 → 大模型选点编排"，
             不提前说清楚，用户会以为页面卡死了。 -->
        <p v-if="submitting" class="section-hint">
          正在汇集候选地点并编排逐日行程，通常需要 15–40 秒，请稍候…
        </p>
      </div>
      <div v-else-if="submitting" class="row" style="gap: 10px; color: var(--text-dim)">
        <span class="spinner" />
        <span style="font-size: 13.5px">正在按这条路线生成行程…</span>
      </div>
    </section>
  </div>
</template>
