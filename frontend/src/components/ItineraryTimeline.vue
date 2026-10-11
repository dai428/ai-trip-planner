<script setup lang="ts">
/**
 * 行程时间轴：按天展示，天内的活动可拖拽重排。
 *
 * 【为什么只允许"天内重排"】
 *    跨天拖动会让"住宿在哪、第几天在哪个城市"这些约束失效。
 *    把可拖范围限制在天内，既给了用户自由，又不会把行程拖崩。
 */
import { ref } from 'vue'
import type { Activity, DayPlan } from '../types'

const props = defineProps<{
  itinerary: DayPlan[]
  readonly?: boolean
}>()

const emit = defineEmits<{
  (e: 'reorder', dayIndex: number, from: number, to: number): void
}>()

const dragDay = ref<number | null>(null)
const dragIdx = ref<number | null>(null)
const overIdx = ref<number | null>(null)

function onStart(dayIndex: number, actIndex: number) {
  if (props.readonly) return
  dragDay.value = dayIndex
  dragIdx.value = actIndex
}

function onOver(dayIndex: number, actIndex: number, e: DragEvent) {
  if (props.readonly || dragDay.value !== dayIndex) return
  e.preventDefault()
  overIdx.value = actIndex
}

function onDrop(dayIndex: number, actIndex: number) {
  if (
    dragDay.value === dayIndex &&
    dragIdx.value !== null &&
    dragIdx.value !== actIndex
  ) {
    emit('reorder', dayIndex, dragIdx.value, actIndex)
  }
  reset()
}

function reset() {
  dragDay.value = null
  dragIdx.value = null
  overIdx.value = null
}

function totalActivities(day: DayPlan) {
  return day.activities.length
}

function kindClass(a: Activity) {
  return a.kind === '美食' ? 'kind-food' : ''
}
</script>

<template>
  <div class="timeline">
    <article
      v-for="(day, di) in itinerary"
      :key="day.day"
      class="day-item fade-in"
      :style="{ animationDelay: `${di * 28}ms` }"
    >
      <div class="card card-pad">
        <div class="row-between wrap" style="margin-bottom: 12px">
          <div class="day-head">
            <span class="day-no">D{{ day.day }}</span>
            <span class="day-city">{{ day.city }}</span>
            <span v-if="day.theme" class="day-theme">{{ day.theme }}</span>
          </div>
          <span class="section-hint">{{ totalActivities(day) }} 项安排</span>
        </div>

        <div class="stack" style="gap: 0">
          <div
            v-for="(act, ai) in day.activities"
            :key="`${act.name}-${ai}`"
            class="act"
            :class="{
              draggable: !readonly,
              dragging: dragDay === di && dragIdx === ai,
              'drop-target': dragDay === di && overIdx === ai && dragIdx !== ai,
            }"
            :draggable="!readonly"
            @dragstart="onStart(di, ai)"
            @dragover="onOver(di, ai, $event)"
            @drop="onDrop(di, ai)"
            @dragend="reset"
          >
            <span v-if="!readonly" class="act-handle" aria-hidden="true">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <circle cx="9" cy="6" r="1.6" /><circle cx="15" cy="6" r="1.6" />
                <circle cx="9" cy="12" r="1.6" /><circle cx="15" cy="12" r="1.6" />
                <circle cx="9" cy="18" r="1.6" /><circle cx="15" cy="18" r="1.6" />
              </svg>
            </span>

            <div class="grow">
              <div class="row wrap" style="gap: 8px">
                <span class="act-kind" :class="kindClass(act)">{{ act.kind }}</span>
                <span class="act-name">{{ act.name }}</span>
              </div>
              <div v-if="act.note" class="act-note">{{ act.note }}</div>
              <span v-if="act.source" class="source-chip" :title="act.source">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M14 3v4a1 1 0 0 0 1 1h4" />
                  <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z" />
                </svg>
                攻略出处：{{ act.source }}
              </span>
            </div>
          </div>
        </div>

        <div v-if="day.meals.length || day.lodging" class="row wrap" style="gap: 8px; margin-top: 13px">
          <span v-if="day.lodging" class="chip">住宿 · {{ day.lodging }}</span>
          <span v-for="m in day.meals" :key="m" class="chip chip-accent">推荐 · {{ m }}</span>
        </div>
      </div>
    </article>
  </div>
</template>
