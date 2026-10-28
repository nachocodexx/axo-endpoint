<template>
  <v-progress-circular indeterminate :size="size" :width="width" :color="color" class="axo-running-ring" aria-label="Running" />
</template>

<script setup lang="ts">
// The one "running" sign: a small ring going round (status chips, step
// cards, lists). Still for people who ask for less motion.
withDefaults(defineProps<{ size?: number; width?: number; color?: string }>(), { size: 14, width: 2, color: 'info' })
</script>

<style scoped>
@media (prefers-reduced-motion: reduce) {
  .axo-running-ring :deep(*) { animation: none !important; }
}
</style>
