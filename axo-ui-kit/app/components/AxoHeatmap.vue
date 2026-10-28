<template>
  <AxoChart :option="option" :height="height" :loading="loading" :empty="total === 0" :empty-text="emptyText" />
</template>

<script setup lang="ts">
// Weekday × hour: when jobs run (darker = more). `cells[weekday][hour]`,
// weekday 0 = Monday (as the Control Plane counts them, in the person's
// time zone); the rows start on the person's first day of the week.
const props = withDefaults(defineProps<{
  cells: number[][]
  weekdays?: string[]
  height?: number
  loading?: boolean
  label?: string
  emptyText?: string
}>(), { weekdays: () => ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], height: 260, label: 'jobs',
  emptyText: 'No jobs in this period yet' })

const c = useChartTheme()
const order = computed(() => weekOrder())
const days = computed(() => order.value.map(i => props.weekdays[i]!))
const rows = computed(() => (props.cells.length ? order.value.map(i => props.cells[i] ?? []) : []))
const hourLabel = (h: number) => (currentSettings().clock === '24h' ? String(h).padStart(2, '0') : `${h % 12 || 12}${h < 12 ? 'a' : 'p'}`)
const total = computed(() => props.cells.flat().reduce((a, b) => a + b, 0))
const max = computed(() => Math.max(1, ...props.cells.flat()))

const option = computed(() => ({
  legend: false,
  grid: { left: 8, right: 8, top: 8, bottom: 40, containLabel: true },
  tooltip: {
    trigger: 'item',
    formatter: (p: any) => `${days.value[p.value[1]]} ${hourLabel(p.value[0])} – ${hourLabel((p.value[0] + 1) % 24)}<br><b>${formatNumber(p.value[2])}</b> ${props.label}`,
  },
  xAxis: {
    type: 'category', data: Array.from({ length: 24 }, (_, h) => hourLabel(h)),
    axisLabel: { color: c.value.muted, interval: 2 }, axisLine: { show: false }, axisTick: { show: false }, splitArea: { show: false },
  },
  yAxis: {
    type: 'category', data: days.value, inverse: true,
    axisLabel: { color: c.value.muted }, axisLine: { show: false }, axisTick: { show: false },
  },
  visualMap: {
    min: 0, max: max.value, calculable: false, orient: 'horizontal', left: 'center', bottom: 0, itemHeight: 120,
    itemWidth: 10, textStyle: { color: c.value.muted },
    inRange: { color: [c.value.dark ? '#1f2a3a' : '#EAF4FF', '#7FC4FF', '#0086FF', '#5B5BFF'] },
  },
  series: [{
    type: 'heatmap',
    data: rows.value.flatMap((row, d) => row.map((v, h) => [h, d, v])),
    itemStyle: { borderRadius: 4, borderColor: c.value.dark ? '#1B1C1F' : '#FFFFFF', borderWidth: 2 },
    emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,134,255,0.4)' } },
  }],
}))
</script>
