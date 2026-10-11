<script setup lang="ts">
/**
 * 路线规划：出发地 → 目的地，判断"中间要不要停、停哪儿"。
 *
 * 【它解决的具体问题】
 *    用户脑子里想的是"我要从北京开车去大理，7 天"，
 *    而不是"我要去哪些城市"。这个模式让他先说清起点和终点，
 *    系统再回答"路太长了，顺路可以在西安、成都停一下"。
 *
 * 【为什么中途推荐要给出"凭什么"】
 *    推荐一个城市而不解释为什么，用户没法判断该不该信。
 *    所以每条都给三样东西：在全程的百分之几处、绕多少公里、当地有多少可玩内容。
 */
import { computed, ref } from 'vue'
import CitySearch from './CitySearch.vue'
import { api } from '../services/api'
import type { RoutePlan } from '../types'

const emit = defineEmits<{
  (e: 'adopt', cities: string[], days: number): void
}>()

const origin = ref('')
const destination = ref('')
const days = ref(7)

const plan = ref<RoutePlan | null>(null)
const loading = ref(false)
const error = ref('')

// 中途站的勾选状态：默认全选（推荐的本来就是最顺路的）
const excluded = ref<Set<string>>(new Set())

/** 最终要采纳的城市：中途站（去掉取消勾选的）+ 目的地 */
const adoptedCities = computed(() => {
  if (!plan.value) return []
  return plan.value.suggested_cities.filter((c) => !excluded.value.has(c))
})

const canPlan = computed(() => !!origin.value.trim() && !!destination.value.trim())

function toggleCity(name: string) {
  const next = new Set(excluded.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  excluded.value = next
}

async function planRoute() {
  if (!canPlan.value) {
    error.value = '请填写出发地和目的地'
    return
  }
  if (origin.value.trim() === destination.value.trim()) {
    error.value = '出发地和目的地相同，不用规划路线'
    return
  }

  loading.value = true
  error.value = ''
  plan.value = null
  excluded.value = new Set()
  try {
    plan.value = await api.planRoute({
      origin: origin.value.trim(),
      destination: destination.value.trim(),
      days: days.value,
    })
  } catch (e) {
    // 后端的 400 会带着"无法识别出发地「xxx」"这样的中文提示，直接展示
    error.value = e instanceof Error ? e.message : '路线规划失败'
  } finally {
    loading.value = false
  }
}

function swap() {
  const t = origin.value
  origin.value = destination.value
  destination.value = t
  plan.value = null
}

function adopt() {
  if (!adoptedCities.value.length) return
  emit('adopt', adoptedCities.value, days.value)
}

const pct = (v: number) => `${Math.round(v * 100)}%`
</script>

<template>
  <div class="stack" style="gap: 18px">
    <!-- 起终点 -->
    <div class="row wrap" style="gap: 12px; align-items: flex-end">
      <div class="field grow" style="min-width: 190px">
        <label class="field-label">出发地</label>
        <CitySearch v-model="origin" placeholder="从哪儿出发，如 北京" />
      </div>

      <button class="btn btn-ghost btn-sm" style="margin-bottom: 9px" title="交换出发地与目的地" @click="swap">
        ⇄
      </button>

      <div class="field grow" style="min-width: 190px">
        <label class="field-label">目的地</label>
        <CitySearch v-model="destination" placeholder="要去哪儿，如 大理" />
      </div>

      <div class="field" style="width: 118px">
        <label class="field-label">几天</label>
        <input v-model.number="days" type="number" min="1" max="30" class="input" />
      </div>

      <button class="btn btn-primary route-submit" style="margin-bottom: 1px" :disabled="loading || !canPlan" @click="planRoute">
        <span v-if="loading" class="spinner" />
        {{ loading ? '规划中…' : '开始规划' }}
      </button>
    </div>

    <div v-if="error" class="alert">{{ error }}</div>

    <!-- 结果 -->
    <div v-if="plan" class="stack fade-in" style="gap: 16px">
      <!-- 结论 -->
      <div class="route-verdict">
        <div class="row wrap" style="gap: 10px">
          <span class="chip chip-primary">{{ plan.total_distance_km }} km</span>
          <span class="chip chip-primary">约 {{ plan.total_duration_hours }} 小时</span>
          <span
            class="chip"
            :class="plan.source === 'amap' ? 'chip-accent' : ''"
            :title="plan.source === 'amap'
              ? '里程与时长来自高德实时路径规划'
              : '未配置高德 Key，按直线距离 × 路网系数估算，仅供参考'"
          >{{ plan.source === 'amap' ? '高德实测' : '本地估算' }}</span>
          <span v-if="plan.needs_stop" class="chip chip-accent">建议中途停留</span>
          <span v-else class="chip">可一天直达</span>
        </div>
        <p style="margin-top: 10px; font-size: 14px; color: var(--text-dim)">{{ plan.advice }}</p>
      </div>

      <!-- 路线链路 -->
      <div class="route-chain">
        <template v-for="(leg, i) in plan.legs" :key="i">
          <div class="route-node">
            <span class="route-node-name">{{ leg.from_city }}</span>
          </div>
          <div class="route-edge">
            <span class="route-edge-line" />
            <span class="route-edge-label">{{ leg.distance_km }} km · {{ leg.duration_hours }}h</span>
          </div>
        </template>
        <div class="route-node route-node-end">
          <span class="route-node-name">{{ plan.destination }}</span>
        </div>
      </div>

      <!-- 中途推荐 -->
      <div v-if="plan.waypoints.length" class="stack" style="gap: 10px">
        <div class="row-between wrap">
          <div class="section-title" style="font-size: 15px">顺路可以去</div>
          <span class="section-hint">取消勾选可跳过某个城市</span>
        </div>
        <div class="waypoint-grid">
          <button
            v-for="w in plan.waypoints"
            :key="w.city"
            class="waypoint-card"
            :aria-pressed="!excluded.has(w.city)"
            @click="toggleCity(w.city)"
          >
            <div class="row-between">
              <span class="wp-city">{{ w.city }}</span>
              <!-- 这里是"当前状态"而不是按钮文案：默认已勾选，
                   写「加入」会让人以为是还没加入、需要点一下 -->
              <span class="wp-check">{{ excluded.has(w.city) ? '已跳过（点击恢复）' : '✓ 已加入' }}</span>
            </div>
            <div class="wp-metrics">
              <span>位于全程 {{ pct(w.progress) }} 处</span>
              <span>绕行 {{ w.detour_km }} km</span>
              <span>攻略 {{ w.attractions }} 处</span>
            </div>
            <div v-if="w.tags.length" class="row wrap" style="gap: 5px; margin-top: 7px">
              <span v-for="t in w.tags.slice(0, 3)" :key="t" class="chip" style="font-size: 11.5px">{{ t }}</span>
            </div>
          </button>
        </div>
      </div>

      <div v-else class="alert alert-info">{{ plan.advice }}</div>

      <!-- 采纳 -->
      <div class="row-between wrap" style="gap: 12px">
        <span class="section-hint">
          {{ adoptedCities.length
            ? `将生成 ${adoptedCities.join(' → ')} · ${days} 天的行程`
            : '请至少保留一个城市' }}
        </span>
        <button class="btn btn-primary" :disabled="!adoptedCities.length" @click="adopt">
          按这条路线生成行程
        </button>
      </div>
    </div>
  </div>
</template>
