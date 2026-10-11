<script setup lang="ts">
/**
 * 分享页（只读）。
 * 通过 /s/:token 访问，任何人拿到链接都能看，但改不了、删不了。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import BudgetChart from '../components/BudgetChart.vue'
import ItineraryTimeline from '../components/ItineraryTimeline.vue'
import PackingPanel from '../components/PackingPanel.vue'
import { api } from '../services/api'
import type { PackingList, TripPlan } from '../types'

const route = useRoute()
const token = computed(() => String(route.params.token))

const plan = ref<TripPlan | null>(null)
const packing = ref<PackingList | null>(null)
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    plan.value = await api.getShared(token.value)
    packing.value = await api.packingList(plan.value.id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '分享链接无效'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="stack" style="gap: 22px">
    <div v-if="loading" class="stack" style="gap: 14px">
      <div class="skeleton" style="height: 96px" />
      <div class="skeleton" style="height: 260px" />
    </div>

    <div v-else-if="error" class="stack" style="gap: 14px">
      <div class="alert">{{ error }}</div>
      <RouterLink to="/" class="btn btn-outline" style="align-self: flex-start">去规划我自己的行程</RouterLink>
    </div>

    <template v-else-if="plan">
      <section class="card card-pad stack fade-in" style="gap: 12px">
        <div class="row wrap" style="gap: 8px">
          <span class="chip chip-accent">他人分享的行程</span>
          <span class="chip">只读</span>
        </div>
        <h1 style="font-size: 24px">{{ plan.cities.join(' → ') }}</h1>
        <div class="row wrap" style="gap: 8px">
          <span class="chip chip-primary">{{ plan.days }} 天</span>
          <span class="chip">{{ plan.pace }}节奏</span>
          <span v-if="plan.start_date" class="chip">出发 {{ plan.start_date }}</span>
        </div>
      </section>

      <section class="stack" style="gap: 12px">
        <div class="section-title">逐日行程</div>
        <ItineraryTimeline :itinerary="plan.itinerary" readonly />
      </section>

      <BudgetChart :items="plan.budget" />
      <PackingPanel v-if="packing" :packing="packing" />

      <RouterLink to="/" class="btn btn-primary" style="align-self: flex-start">
        用行迹规划我自己的行程
      </RouterLink>
    </template>
  </div>
</template>
