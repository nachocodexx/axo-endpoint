<template>
  <AxoChart :option="option" :height="height" :loading="loading" :empty="!hasData" :empty-text="emptyText" />
</template>

<script setup lang="ts">
import { SERIES_PALETTE } from '../utils/status'

// Values over time: lines (soft gradient area under them) or stacked bars,
// zoomable by dragging (`zoom`). Points are [unix seconds, value].
export interface TimeSeries {
  name: string
  points: Array<[number, number | null]>
  color?: string
  /** Series with the same stack name pile up (completed + failed jobs). */
  stack?: string
  type?: 'line' | 'bar'
  dashed?: boolean
}

const props = withDefaults(defineProps<{
  series: TimeSeries[]
  height?: number
  loading?: boolean
  area?: boolean
  zoom?: boolean
  /** Formats the values (axis + tooltip), e.g. formatMs. */
  format?: (v: number) => string
  emptyText?: string
}>(), { height: 280, loading: false, area: true, zoom: false })

const c = useChartTheme()
const isolated = (points: Array<[number, number | null]>, j: number) =>
  points[j]![1] != null && points[j - 1]?.[1] == null && points[j + 1]?.[1] == null
const hasData = computed(() => props.series.some(s => s.points.some(p => p[1] != null)))

function fade(hex: string, alpha: number) {
  const n = Number.parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

// Times are written in the person's settings (ECharts alone would use the
// browser's own time zone and style).
const span = computed(() => {
  const times = props.series.flatMap(s => s.points.map(p => p[0]))
  return times.length ? Math.max(...times) - Math.min(...times) : 86400
})

const option = computed(() => {
  const fmt = props.format ?? ((v: number) => formatNumber(v))
  return {
    grid: { left: 8, right: 16, top: 36, bottom: props.zoom ? 44 : 8, containLabel: true },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: c.value.grid } },
      axisLabel: { color: c.value.muted, hideOverlap: true, formatter: (ms: number) => formatAxisTime(ms / 1000, span.value) },
      axisPointer: { label: { formatter: (p: { value: number }) => formatDateTime(p.value / 1000) } },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: c.value.muted, formatter: (v: number) => fmt(v) },
      splitLine: { lineStyle: { color: c.value.grid } },
    },
    tooltip: {
      valueFormatter: (v: number | null) => (v == null ? '—' : fmt(v)),
    },
    dataZoom: props.zoom
      ? [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8, borderColor: 'transparent', fillerColor: 'rgba(0,134,255,0.15)' }]
      : undefined,
    series: props.series.map((s, i) => {
      const color = s.color ?? SERIES_PALETTE[i % SERIES_PALETTE.length]!
      const type = s.type ?? 'line'
      const hasIsolated = type === 'line' && s.points.some((_, j) => isolated(s.points, j))
      return {
        name: s.name,
        type,
        stack: s.stack,
        // A point with no neighbour draws no line: it shows as a dot instead.
        data: s.points.map(([t, v], j) => {
          if (!hasIsolated) return [t * 1000, v]
          return { value: [t * 1000, v], symbol: isolated(s.points, j) ? 'circle' : 'none', symbolSize: 7 }
        }),
        showSymbol: hasIsolated,
        smooth: 0.25,
        connectNulls: false,
        barMaxWidth: 18,
        itemStyle: { color, borderRadius: type === 'bar' ? [3, 3, 0, 0] : undefined },
        lineStyle: { width: 2, type: s.dashed ? 'dashed' : 'solid', color },
        emphasis: { focus: 'series' },
        areaStyle: type === 'line' && props.area && !s.dashed
          ? { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
            { offset: 0, color: fade(color, 0.28) }, { offset: 1, color: fade(color, 0.02) }] } }
          : undefined,
      }
    }),
  }
})
</script>
