<template>
  <v-card :class="['axo-panel', { 'h-100': !stacked }]">
    <div class="d-flex align-center flex-wrap ga-2 px-4 pt-4" :class="dense ? 'pb-1' : 'pb-2'">
      <v-icon v-if="icon" color="primary" size="20">{{ icon }}</v-icon>
      <span class="text-subtitle-1 font-weight-medium">{{ title }}</span>
      <AxoHelp v-if="help" :title="title" :text="help" />
      <v-spacer />
      <slot name="actions" />
    </div>
    <div v-if="subtitle" class="px-4 text-caption axo-muted">{{ subtitle }}</div>
    <div :class="flush ? '' : 'pa-4 pt-2'"><slot /></div>
  </v-card>
</template>

<script setup lang="ts">
// A titled card (with "?" help and actions) that holds a chart or a list.
// It fills its grid cell (equal heights in a row) unless `stacked` (panels
// one under the other in a column keep their own height).
defineProps<{ title: string; subtitle?: string; icon?: string; help?: string; flush?: boolean; dense?: boolean; stacked?: boolean }>()
</script>
