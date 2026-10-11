<script setup lang="ts">
/**
 * 城市选择器：搜索 + 点选目的地 + 拖拽调整游览顺序。
 *
 * 【为什么顺序要能拖】
 *    「大理 → 成都 → 西安」和「西安 → 成都 → 大理」是完全不同的两条路线，
 *     顺序本身是用户的核心决策，不能让程序替他们定。
 *
 * 【为什么要有搜索框】
 *     下面的卡片只有攻略库收录的 10 座城市。没有搜索框的话，
 *     用户会以为"这个产品只能去这十个地方"。
 *     搜索框把可选范围扩到全国，并用「有攻略 / 仅定位」标出差别。
 */
import { computed, ref } from 'vue'
import CitySearch from './CitySearch.vue'
import type { CityMeta, CitySuggestion } from '../types'

const props = defineProps<{
  cities: CityMeta[]
  modelValue: string[]
  max?: number
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

const maxPick = computed(() => props.max ?? 6)
const isPicked = (name: string) => props.modelValue.includes(name)
const orderOf = (name: string) => props.modelValue.indexOf(name) + 1

const dragFrom = ref<number | null>(null)
const dragOver = ref<number | null>(null)
const searchTip = ref('')

function toggle(name: string) {
  const next = [...props.modelValue]
  const idx = next.indexOf(name)
  if (idx >= 0) {
    next.splice(idx, 1)
  } else {
    if (next.length >= maxPick.value) return
    next.push(name)
  }
  emit('update:modelValue', next)
}

/** 从搜索结果里加一个城市（可能不在攻略库内） */
function pickSearched(c: CitySuggestion) {
  if (isPicked(c.name)) {
    searchTip.value = `${c.name} 已在行程里`
    return
  }
  if (props.modelValue.length >= maxPick.value) {
    searchTip.value = `最多只能选 ${maxPick.value} 个城市`
    return
  }
  emit('update:modelValue', [...props.modelValue, c.name])
  searchTip.value = c.has_guide
    ? `已加入 ${c.name} · 本地有攻略`
    : `已加入 ${c.name} · 暂无本地攻略，行程将由高德 POI 兜底`
}

function onDragStart(i: number) {
  dragFrom.value = i
}

function onDragOver(i: number, e: DragEvent) {
  e.preventDefault()
  dragOver.value = i
}

function onDrop(i: number) {
  const from = dragFrom.value
  if (from !== null && from !== i) {
    const next = [...props.modelValue]
    const [moved] = next.splice(from, 1)
    next.splice(i, 0, moved)
    emit('update:modelValue', next)
  }
  dragFrom.value = null
  dragOver.value = null
}

function onDragEnd() {
  dragFrom.value = null
  dragOver.value = null
}
</script>

<template>
  <div class="stack" style="gap: 14px">
    <!-- 搜索：可选范围是全国的任意城市 -->
    <div class="stack" style="gap: 6px">
      <div class="row-between">
        <span class="field-label">搜索城市</span>
        <span class="section-hint">全国范围，有攻略的城市玩得更细</span>
      </div>
      <CitySearch
        :model-value="''"
        clear-on-pick
        placeholder="输入城市名，如 成都 / 张家界 / 呼伦贝尔"
        @pick="pickSearched"
      />
      <div v-if="searchTip" class="section-hint">{{ searchTip }}</div>
    </div>

    <!-- 已选城市：可拖拽排序 -->
    <div v-if="modelValue.length" class="stack" style="gap: 8px">
      <div class="row" style="gap: 8px">
        <span class="field-label">游览顺序</span>
        <span class="section-hint">拖动调整先后，第 1 个城市最先去</span>
      </div>
      <ul class="picked-list">
        <li
          v-for="(name, i) in modelValue"
          :key="name"
          class="picked-item"
          :class="{ dragging: dragFrom === i, 'drop-target': dragOver === i && dragFrom !== i }"
          draggable="true"
          @dragstart="onDragStart(i)"
          @dragover="onDragOver(i, $event)"
          @drop="onDrop(i)"
          @dragend="onDragEnd"
        >
          <span style="opacity: 0.65; font-size: 12px">{{ i + 1 }}</span>
          <span>{{ name }}</span>
          <button class="picked-remove" title="移除" @click.stop="toggle(name)">×</button>
        </li>
      </ul>
    </div>

    <!-- 候选城市 -->
    <div class="stack" style="gap: 8px">
      <div class="row-between">
        <span class="field-label">热门目的地</span>
        <span class="section-hint">已选 {{ modelValue.length }} / {{ maxPick }}</span>
      </div>
      <div class="city-grid">
        <button
          v-for="c in cities"
          :key="c.name"
          class="city-card"
          :aria-pressed="isPicked(c.name)"
          :disabled="!isPicked(c.name) && modelValue.length >= maxPick"
          @click="toggle(c.name)"
        >
          <span v-if="isPicked(c.name)" class="city-order">{{ orderOf(c.name) }}</span>
          <div class="city-name">{{ c.name }}</div>
          <div class="city-meta">{{ c.tags.slice(0, 2).join(' · ') }} · 建议 {{ c.avg_days }} 天</div>
        </button>
      </div>
    </div>
  </div>
</template>
