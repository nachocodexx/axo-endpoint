<template>
  <ClientOnly>
    <VChart v-if="values.length > 1" :option="option" autoresize :style="{ height: `${height}px`, width: '100%' }" />
    <div v-else :style="{ height: `${height}px` }" />
  </ClientOnly>
</template>

<script setup lang="ts">
// A tiny trend line for a figure card (no axes, no tooltip).
const props = withDefaults(defineProps<{ values: Array<number | null>; color?: string; height?: number }>(),
  { color: '#0086FF', height: 40 })
const option = computed(() => ({
  animationDuration: 500,
  grid: { left: 0, right: 0, top: 4, bottom: 0 },
  xAxis: { type: 'category', show: false, boundaryGap: false },
  yAxis: { type: 'value', show: false, scale: true },
  series: [{
    type: 'line', data: props.values, showSymbol: false, smooth: 0.3,
    lineStyle: { width: 2, color: props.color },
    areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
      { offset: 0, color: `${props.color}55` }, { offset: 1, color: `${props.color}00` }] } },
  }],
}))
</script>
