<template>
  <div class="axo-skeleton">
    <template v-if="type === 'card'">
      <div class="axo-shimmer" :style="{ height: `${height}px` }" />
    </template>
    <template v-else>
      <div v-for="i in lines" :key="i" class="axo-shimmer mb-2"
           :style="{ height: '14px', width: i === lines ? '60%' : '100%' }" />
    </template>
  </div>
</template>

<script setup lang="ts">
// Shimmering placeholder while data loads (keeps the layout from jumping).
withDefaults(defineProps<{ type?: 'lines' | 'card'; lines?: number; height?: number }>(),
  { type: 'lines', lines: 3, height: 120 })
</script>
