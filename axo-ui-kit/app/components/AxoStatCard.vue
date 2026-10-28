<template>
  <v-card class="axo-stat pa-4 h-100" :class="{ 'axo-stat--link': to }" @click="to && navigateTo(to)" :ripple="!!to">
    <div class="d-flex align-center">
      <v-avatar v-if="icon" size="34" rounded="lg" class="axo-soft mr-3">
        <v-icon :color="color" size="20">{{ icon }}</v-icon>
      </v-avatar>
      <span class="text-body-2 axo-muted">{{ label }}</span>
      <AxoHelp v-if="help" :title="label" :text="help" />
    </div>
    <AxoSkeleton v-if="loading" :lines="1" class="mt-4" />
    <template v-else>
      <div class="d-flex align-end flex-wrap ga-2 mt-3">
        <span class="axo-stat__value">{{ value ?? '—' }}</span>
        <span v-if="unit" class="text-body-2 axo-muted mb-1">{{ unit }}</span>
        <v-spacer />
        <v-chip v-if="change != null" size="x-small" :color="changeColor" variant="tonal" class="mb-1">
          <v-icon start size="12">{{ change >= 0 ? 'mdi-arrow-up' : 'mdi-arrow-down' }}</v-icon>
          {{ Math.abs(change) < 1000 ? Math.abs(change).toFixed(0) : '999+' }} %
          <v-tooltip activator="parent">{{ changeLabel }}</v-tooltip>
        </v-chip>
      </div>
      <div v-if="caption" class="text-caption axo-muted mt-1">{{ caption }}</div>
      <AxoSparkline v-if="trend?.length" :values="trend" :color="sparkColor" class="mt-2" />
    </template>
  </v-card>
</template>

<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import { CHART_COLORS } from '../utils/status'

// A key figure: label (+ "?"), value, change vs the previous period and a
// sparkline. `goodWhenUp=false` for figures where up is bad (failures, p95).
const props = withDefaults(defineProps<{
  label: string
  value?: string | number | null
  unit?: string
  icon?: string
  color?: string
  help?: string
  caption?: string
  change?: number | null
  changeLabel?: string
  goodWhenUp?: boolean
  trend?: Array<number | null>
  loading?: boolean
  to?: RouteLocationRaw
}>(), { color: 'primary', goodWhenUp: true, changeLabel: 'Change vs the previous period', loading: false })

const changeColor = computed(() => {
  if (props.change == null || Math.abs(props.change) < 0.5) return 'muted'
  return (props.change > 0) === props.goodWhenUp ? 'success' : 'error'
})
const sparkColor = computed(() => ({ success: CHART_COLORS.green, error: CHART_COLORS.red, warning: CHART_COLORS.orange,
  'brand-3': CHART_COLORS.violet } as Record<string, string>)[props.color] ?? CHART_COLORS.blue)
</script>

<style scoped>
.axo-stat__value { font-size: 1.75rem; font-weight: 700; line-height: 1.1; letter-spacing: -0.01em; }
.axo-stat { transition: box-shadow 0.2s ease, transform 0.2s ease; }
.axo-stat--link { cursor: pointer; }
.axo-stat--link:hover { box-shadow: 0 8px 24px rgba(0, 134, 255, 0.14); transform: translateY(-1px); }
</style>
