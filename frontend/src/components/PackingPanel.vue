<script setup lang="ts">
/**
 * 打包清单面板：勾选式，带完成进度。
 *
 * 勾选状态存在本地组件里（不落库）——
 * 它是"出行前的临时动作"，不是行程的一部分，没必要污染后端。
 */
import { computed, reactive } from 'vue'
import type { PackingList } from '../types'

const props = defineProps<{ packing: PackingList }>()

const checked = reactive<Record<string, boolean>>({})

const allItems = computed(() =>
  props.packing.categories.flatMap((c) => c.items.map((i) => `${c.category}::${i}`)),
)

const doneCount = computed(() => allItems.value.filter((k) => checked[k]).length)
const progress = computed(() =>
  allItems.value.length ? Math.round((doneCount.value / allItems.value.length) * 100) : 0,
)

function keyOf(cat: string, item: string) {
  return `${cat}::${item}`
}
</script>

<template>
  <section class="card card-pad stack" style="gap: 16px">
    <div class="row-between wrap">
      <div class="section-title">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
          <rect x="8" y="2" width="8" height="4" rx="1" />
          <path d="M9 13l2 2 4-4" />
        </svg>
        出行打包清单
      </div>
      <span class="section-hint">{{ doneCount }} / {{ allItems.length }} 已备齐 · {{ progress }}%</span>
    </div>

    <div class="budget-bar">
      <span :style="{ width: progress + '%' }" />
    </div>

    <p class="section-hint">
      按 {{ packing.cities.join(' → ') }} · {{ packing.days }} 天 · 出发季节自动生成。
      勾选状态仅保存在本机浏览器。
    </p>

    <div class="pack-grid">
      <div v-for="cat in packing.categories" :key="cat.category">
        <div class="pack-cat-title">{{ cat.category }}</div>
        <label v-for="item in cat.items" :key="item" class="pack-item">
          <input type="checkbox" v-model="checked[keyOf(cat.category, item)]" />
          <span>{{ item }}</span>
        </label>
      </div>
    </div>
  </section>
</template>
