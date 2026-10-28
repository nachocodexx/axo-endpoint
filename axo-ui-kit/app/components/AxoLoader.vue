<template>
  <div :class="['axo-loader', { 'axo-loader--overlay': overlay, 'axo-loader--block': block }]">
    <div class="axo-orbit" :style="{ width: `${size}px`, height: `${size}px` }" role="progressbar" aria-busy="true">
      <span /><span /><span />
    </div>
    <div v-if="text" class="text-body-2 axo-muted mt-3">{{ text }}</div>
  </div>
</template>

<script setup lang="ts">
// The axo loading animation: three orbits in the brand colors.
// `block` centers it in its area; `overlay` covers its (relative) parent.
withDefaults(defineProps<{ size?: number; text?: string; overlay?: boolean; block?: boolean }>(),
  { size: 48, overlay: false, block: false })
</script>

<style scoped>
.axo-loader { display: inline-flex; flex-direction: column; align-items: center; }
.axo-loader--block { display: flex; justify-content: center; padding: 48px 0; width: 100%; }
.axo-loader--overlay {
  position: absolute; inset: 0; z-index: 5; display: flex; justify-content: center;
  background: rgba(var(--v-theme-surface), 0.6); backdrop-filter: blur(1px);
}
</style>
