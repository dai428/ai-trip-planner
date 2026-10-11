<script setup lang="ts">
/** 我的行程：历史列表，支持查看与删除。 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../services/api'
import type { TripSummary } from '../types'

const items = ref<TripSummary[]>([])
const loading = ref(true)
const error = ref('')
const deleting = ref<string | null>(null)

async function load() {
  loading.value = true
  try {
    items.value = await api.history()
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function remove(id: string) {
  deleting.value = id
  try {
    await api.removeTrip(id)
    items.value = items.value.filter((t) => t.id !== id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
  } finally {
    deleting.value = null
  }
}

function fmt(iso: string) {
  const d = new Date(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

onMounted(load)
</script>

<template>
  <div class="stack" style="gap: 20px">
    <div class="row-between wrap">
      <h1 style="font-size: 24px">我的行程</h1>
      <RouterLink to="/" class="btn btn-primary btn-sm">新建行程</RouterLink>
    </div>

    <div v-if="error" class="alert">{{ error }}</div>

    <div v-if="loading" class="stack" style="gap: 12px">
      <div v-for="i in 3" :key="i" class="skeleton" style="height: 76px" />
    </div>

    <div v-else-if="!items.length" class="empty">
      <p style="font-size: 15px; color: var(--text-dim)">还没有保存的行程</p>
      <p style="margin-top: 6px">去首页规划第一趟旅程吧</p>
      <RouterLink to="/" class="btn btn-primary" style="margin-top: 18px">开始规划</RouterLink>
    </div>

    <div v-else class="stack" style="gap: 12px">
      <article
        v-for="t in items"
        :key="t.id"
        class="card card-pad card-hover row-between wrap fade-in"
        style="gap: 12px"
      >
        <div class="stack" style="gap: 5px">
          <RouterLink :to="{ name: 'trip', params: { id: t.id } }" style="font-size: 16px; font-weight: 620">
            {{ t.cities.join(' → ') }}
          </RouterLink>
          <div class="row wrap" style="gap: 8px">
            <span class="chip chip-primary">{{ t.days }} 天</span>
            <span class="section-hint">生成于 {{ fmt(t.created_at) }}</span>
          </div>
        </div>
        <div class="row" style="gap: 8px">
          <RouterLink :to="{ name: 'trip', params: { id: t.id } }" class="btn btn-outline btn-sm">查看</RouterLink>
          <button class="btn btn-danger btn-sm" :disabled="deleting === t.id" @click="remove(t.id)">
            {{ deleting === t.id ? '删除中…' : '删除' }}
          </button>
        </div>
      </article>
    </div>
  </div>
</template>
