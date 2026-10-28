<template>
  <div class="axo-chart" :style="{ height: `${height}px` }">
    <ClientOnly>
      <VChart v-if="!empty" class="axo-chart__canvas" :option="merged" autoresize :update-options="{ notMerge: true }" />
      <template #fallback><AxoSkeleton type="card" :height="height" /></template>
    </ClientOnly>
    <div v-if="empty && !loading" class="axo-chart__empty">
      <v-icon color="muted" size="28">mdi-chart-line-variant</v-icon>
      <div class="text-body-2 axo-muted mt-1">{{ emptyText }}</div>
    </div>
    <AxoLoader v-if="loading" overlay :size="36" />
  </div>
</template>

<script setup lang="ts">
// Any ECharts chart with the axo look (fonts, grid, tooltip, dark mode),
// a loading overlay and an empty message. Pass a normal ECharts `option`.
const props = withDefaults(defineProps<{
  option: Record<string, any>
  height?: number
  loading?: boolean
  empty?: boolean
  emptyText?: string
}>(), { height: 280, loading: false, empty: false, emptyText: 'No data in this period yet' })

const c = useChartTheme()
const merged = computed(() => ({
  backgroundColor: 'transparent',
  textStyle: { fontFamily: 'inherit', color: c.value.text },
  animationDuration: 600,
  animationEasing: 'cubicOut',
  ...props.option,
  tooltip: props.option.tooltip === false ? undefined : {
    trigger: 'axis',
    backgroundColor: c.value.tooltipBg,
    borderColor: c.value.tooltipBorder,
    textStyle: { color: c.value.text, fontSize: 12 },
    extraCssText: 'box-shadow: 0 8px 24px rgba(0,0,0,.15); border-radius: 10px;',
    ...(props.option.tooltip || {}),
  },
  legend: props.option.legend === false ? undefined : {
    top: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 8, textStyle: { color: c.value.text },
    ...(props.option.legend || {}),
  },
}))
</script>

<style scoped>
.axo-chart { position: relative; width: 100%; }
.axo-chart__canvas { width: 100%; height: 100%; }
.axo-chart__empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
</style>
