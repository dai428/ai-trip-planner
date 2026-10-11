<script setup lang="ts">
/**
 * 结果页：展示行程，提供四件"落地"的事 ——
 *   ① 拖拽微调  ② 生成打包清单  ③ 导出 ICS 日历  ④ 生成分享链接
 *
 * 【一个诚实的设计决定】
 *   拖拽调整只改本地视图，不回写后端。理由：
 *   行程是"生成物"，用户手动微调属于个人笔记性质；
 *   真要做服务端持久化，需要引入版本号与冲突合并，属于下一阶段的事。
 *   与其假装保存了，不如在界面上明说 —— 这比"以为存了其实没存"友好得多。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import BudgetChart from '../components/BudgetChart.vue'
import ItineraryTimeline from '../components/ItineraryTimeline.vue'
import PackingPanel from '../components/PackingPanel.vue'
import { api } from '../services/api'
import type { PackingList, TripPlan } from '../types'

const route = useRoute()
const tripId = computed(() => String(route.params.id))

const plan = ref<TripPlan | null>(null)
const packing = ref<PackingList | null>(null)
const packingLoading = ref(false)
const showPacking = ref(true)

const loading = ref(true)
const error = ref('')

const shareUrl = ref('')
const shareBusy = ref(false)
const copied = ref(false)

const dirty = ref(false)   // 是否被本地拖拽改过

function reorder(dayIndex: number, from: number, to: number) {
  if (!plan.value) return
  const acts = plan.value.itinerary[dayIndex].activities
  const [moved] = acts.splice(from, 1)
  acts.splice(to, 0, moved)
  dirty.value = true
}

async function exportCalendar() {
  if (!plan.value) return
  // 交给浏览器下载：ICS 是文件流，用 <a download> 比 fetch 更省事
  const a = document.createElement('a')
  a.href = api.icsUrl(plan.value.id)
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

async function makeShare() {
  if (!plan.value) return
  shareBusy.value = true
  try {
    const info = await api.createShare(plan.value.id)
    shareUrl.value = info.url
    await copyShare()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '分享失败'
  } finally {
    shareBusy.value = false
  }
}

async function copyShare() {
  if (!shareUrl.value) return
  try {
    await navigator.clipboard.writeText(shareUrl.value)
  } catch {
    // 非 HTTPS 或旧浏览器下 clipboard API 不可用，降级为选中提示
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2200)
}

async function loadPacking() {
  if (packing.value || packingLoading.value) return
  packingLoading.value = true
  try {
    packing.value = await api.packingList(tripId.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '打包清单加载失败'
  } finally {
    packingLoading.value = false
  }
}

function togglePacking() {
  showPacking.value = !showPacking.value
  if (showPacking.value) loadPacking()
}

const totalBudget = computed(() =>
  plan.value ? plan.value.budget.reduce((s, b) => s + b.amount, 0) : 0,
)

const dateRange = computed(() => {
  if (!plan.value?.start_date) return ''
  const start = new Date(plan.value.start_date + 'T00:00:00')
  const end = new Date(start)
  end.setDate(end.getDate() + plan.value.days - 1)
  const f = (d: Date) => `${d.getMonth() + 1}月${d.getDate()}日`
  return `${f(start)} – ${f(end)}`
})

onMounted(async () => {
  try {
    plan.value = await api.getTrip(tripId.value)
    loadPacking()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '行程加载失败'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="stack" style="gap: 22px">
    <div v-if="loading" class="stack" style="gap: 14px">
      <div class="skeleton" style="height: 96px" />
      <div class="skeleton" style="height: 220px" />
      <div class="skeleton" style="height: 220px" />
    </div>

    <div v-else-if="error && !plan" class="stack" style="gap: 14px">
      <div class="alert">{{ error }}</div>
      <RouterLink to="/" class="btn btn-outline" style="align-self: flex-start">← 返回重新规划</RouterLink>
    </div>

    <template v-else-if="plan">
      <!-- 概览与操作栏 -->
      <section class="card card-pad stack fade-in" style="gap: 16px">
        <div class="row-between wrap" style="gap: 14px">
          <div class="stack" style="gap: 6px">
            <h1 style="font-size: 24px">{{ plan.cities.join(' → ') }}</h1>
            <div class="row wrap" style="gap: 8px">
              <span class="chip chip-primary">{{ plan.days }} 天</span>
              <span v-if="dateRange" class="chip">{{ dateRange }}</span>
              <span class="chip">{{ plan.pace }}节奏</span>
              <span class="chip">预算约 ¥{{ Math.round(totalBudget).toLocaleString('zh-CN') }}</span>
            </div>
          </div>

          <div class="row wrap" style="gap: 9px">
            <RouterLink to="/" class="btn btn-ghost btn-sm">重新规划</RouterLink>
            <button class="btn btn-outline btn-sm" :disabled="shareBusy" @click="makeShare">
              <span v-if="shareBusy" class="spinner" style="width: 13px; height: 13px" />
              分享
            </button>
            <button class="btn btn-primary btn-sm" @click="exportCalendar">导出日历</button>
          </div>
        </div>

        <div v-if="shareUrl" class="alert-info alert row-between wrap" style="gap: 10px">
          <span style="word-break: break-all">分享链接：{{ shareUrl }}</span>
          <button class="btn btn-sm btn-outline" @click="copyShare">
            {{ copied ? '已复制' : '复制' }}
          </button>
        </div>

        <p v-if="dirty" class="section-hint">
          你已手动调整过顺序——调整只作用于当前页面，刷新后会回到原始安排。
        </p>
      </section>

      <!-- 行程时间轴 -->
      <section class="stack" style="gap: 12px">
        <div class="row-between">
          <div class="section-title">逐日行程</div>
          <span class="section-hint">拖动左侧手柄可调整当天顺序</span>
        </div>
        <ItineraryTimeline :itinerary="plan.itinerary" @reorder="reorder" />
      </section>

      <!-- 预算 -->
      <BudgetChart :items="plan.budget" />

      <!-- 打包清单 -->
      <section class="stack" style="gap: 12px">
        <div class="row-between">
          <div class="section-title">打包清单</div>
          <button class="btn btn-ghost btn-sm" @click="togglePacking">
            {{ showPacking ? '收起' : '展开' }}
          </button>
        </div>
        <div v-if="showPacking">
          <div v-if="packingLoading && !packing" class="skeleton" style="height: 200px" />
          <PackingPanel v-else-if="packing" :packing="packing" />
        </div>
      </section>
    </template>
  </div>
</template>
