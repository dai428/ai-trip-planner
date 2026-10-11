<script setup lang="ts">
/**
 * 预算拆分图：横向条形图。
 *
 * 自己用 div + CSS 画，不引图表库 ——
 * 五条数据引一个 ECharts 是过度工程；条形图用 flex + width 就够了。
 */
import { computed } from 'vue'
import type { BudgetItem } from '../types'

const props = defineProps<{ items: BudgetItem[]; perPerson?: boolean }>()

const total = computed(() => props.items.reduce((s, i) => s + i.amount, 0))
const max = computed(() => Math.max(...props.items.map((i) => i.amount), 1))

function pct(v: number) {
  return `${Math.max((v / max.value) * 100, 3)}%`
}

function yuan(v: number) {
  return `¥${Math.round(v).toLocaleString('zh-CN')}`
}
</script>

<template>
  <section class="card card-pad stack" style="gap: 14px">
    <div class="row-between wrap">
      <div class="section-title">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
        </svg>
        预算拆分
      </div>
      <span class="section-hint">估算 · 人均</span>
    </div>

    <div class="stack" style="gap: 11px">
      <div v-for="item in items" :key="item.label" class="budget-row">
        <span style="font-size: 13px; color: var(--text-dim)">{{ item.label }}</span>
        <div class="budget-bar"><span :style="{ width: pct(item.amount) }" /></div>
        <span class="budget-amount">{{ yuan(item.amount) }}</span>
      </div>
    </div>

    <div class="row-between" style="border-top: 1px solid var(--border); padding-top: 12px">
      <span style="font-weight: 620">合计</span>
      <span style="font-weight: 700; font-size: 18px; font-variant-numeric: tabular-nums">
        {{ yuan(total) }}
      </span>
    </div>

    <p class="section-hint">
      按城市消费水平、天数与景点数量估算，不含往返大交通与购物。实际花费随季节与个人选择浮动。
    </p>
  </section>
</template>
