<script setup lang="ts">
/**
 * 城市搜索框（全国范围）。
 *
 * 【为什么不用下拉选择器，而是做一个搜索框】
 *    攻略库只收录了 10 座城市，但可选目的地其实是全国任意城市。
 *    固定列表会让用户以为"只能去这十个地方"，
 *    搜索框才能表达"哪儿都能去，只是有攻略的城市玩得更细"。
 *
 * 【两个必须处理好的细节】
 *   1. 防抖 + 竞态丢弃：边打字边请求，早发的请求可能后返回，
 *      如果不按序号丢弃过期响应，结果会被旧关键词覆盖 —— 看起来像"搜错了"。
 *   2. 键盘可达：↑↓ 选择、Enter 确认、Esc 关闭，
 *      只支持鼠标点选的搜索框在真实使用里很难受。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../services/api'
import type { CitySuggestion } from '../types'

const props = defineProps<{
  placeholder?: string
  /** 已选中的城市名（用于回显） */
  modelValue?: string
  /** 选完后是否清空输入框 —— "连续添加"场景下需要 */
  clearOnPick?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'pick', value: CitySuggestion): void
}>()

const keyword = ref(props.modelValue ?? '')
const results = ref<CitySuggestion[]>([])
const open = ref(false)
const loading = ref(false)
const error = ref('')
const activeIndex = ref(-1)

// 竞态控制：每次请求领一个号，回来时不是最新号就直接扔掉
let seq = 0
let timer: ReturnType<typeof setTimeout> | null = null

watch(() => props.modelValue, (v) => {
  if (v !== keyword.value) keyword.value = v ?? ''
})

watch(keyword, (q) => {
  if (timer) clearTimeout(timer)
  activeIndex.value = -1

  const trimmed = q.trim()
  if (!trimmed) {
    results.value = []
    open.value = false
    loading.value = false
    return
  }

  loading.value = true
  open.value = true
  timer = setTimeout(() => run(trimmed), 220)
})

async function run(q: string) {
  const mine = ++seq
  try {
    const resp = await api.searchCities(q)
    if (mine !== seq) return          // 已有更新的请求发出，丢弃本次结果
    results.value = resp.results
    error.value = ''
  } catch (e) {
    if (mine !== seq) return
    results.value = []
    error.value = e instanceof Error ? e.message : '搜索失败'
  } finally {
    if (mine === seq) loading.value = false
  }
}

function choose(c: CitySuggestion) {
  keyword.value = props.clearOnPick ? '' : c.name
  emit('update:modelValue', c.name)
  emit('pick', c)
  open.value = false
  activeIndex.value = -1
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    open.value = false
    return
  }
  if (!results.value.length) {
    // 没有候选时，回车就把当前输入原样当城市名交出去（后端会校验并给出提示）
    if (e.key === 'Enter' && keyword.value.trim()) {
      emit('update:modelValue', keyword.value.trim())
      open.value = false
    }
    return
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    open.value = true
    activeIndex.value = (activeIndex.value + 1) % results.value.length
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    activeIndex.value = (activeIndex.value - 1 + results.value.length) % results.value.length
  } else if (e.key === 'Enter') {
    e.preventDefault()
    choose(results.value[activeIndex.value >= 0 ? activeIndex.value : 0])
  }
}

/** 失焦后延迟关闭，否则点击候选项时会先触发 blur 导致点不中 */
function onBlur() {
  setTimeout(() => { open.value = false }, 160)
}

onBeforeUnmount(() => { if (timer) clearTimeout(timer) })
</script>

<template>
  <div class="search-box">
    <div class="search-input-wrap">
      <span class="search-icon">◎</span>
      <input
        v-model="keyword"
        class="input search-input"
        type="text"
        autocomplete="off"
        :placeholder="placeholder ?? '搜索城市，如 成都 / 张家界'"
        @focus="open = !!results.length"
        @keydown="onKeydown"
        @blur="onBlur"
      />
      <span v-if="loading" class="spinner search-spinner" />
    </div>

    <div v-if="open" class="search-panel">
      <div v-if="error" class="search-empty">{{ error }}</div>
      <div v-else-if="!results.length && !loading" class="search-empty">
        没有找到「{{ keyword.trim() }}」，换个叫法试试
      </div>
      <ul v-else>
        <li
          v-for="(c, i) in results"
          :key="c.name"
          class="search-option"
          :class="{ active: i === activeIndex }"
          @mousedown.prevent="choose(c)"
          @mouseenter="activeIndex = i"
        >
          <span class="so-name">{{ c.name }}</span>
          <span v-if="c.province" class="so-province">{{ c.province }}</span>
          <span
            class="so-badge"
            :class="c.has_guide ? 'so-badge-guide' : 'so-badge-poi'"
            :title="c.has_guide
              ? '本地攻略库已收录，行程由攻略检索生成，每条都带出处'
              : '暂无本地攻略，行程由高德 POI 兜底生成'"
          >{{ c.has_guide ? '有攻略' : '仅定位' }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>
