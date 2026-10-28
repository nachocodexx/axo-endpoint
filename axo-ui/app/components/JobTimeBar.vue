<template>
  <div>
    <div class="axo-timebar" role="img" :aria-label="summary">
      <div v-for="s in segments" :key="s.key" class="axo-timebar-part" :style="{ flexGrow: Math.max(s.ms, total * 0.012), background: s.color }">
        <v-tooltip activator="parent" location="top">
          <b>{{ s.label }}</b> · {{ formatMs(s.ms) }}<br><span v-if="s.text">{{ s.text }}</span>
        </v-tooltip>
      </div>
    </div>
    <div class="d-flex flex-wrap ga-3 mt-2">
      <span v-for="s in segments" :key="s.key" class="text-caption d-inline-flex align-center ga-1">
        <span class="axo-dot" :style="{ background: s.color }" />{{ s.label }} {{ formatMs(s.ms) }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { JobAttempt } from '../utils/jobs'

// Where an attempt's time went, layer by layer (front door → VE → endpoint
// queue → runner → running), each a coloured part; hover for the ms.
const props = defineProps<{ attempt: JobAttempt }>()
const segments = computed(() => attemptSegments(props.attempt))
const total = computed(() => segments.value.reduce((a, s) => a + s.ms, 0))
const summary = computed(() => segments.value.map(s => `${s.label} ${formatMs(s.ms)}`).join(', '))
</script>

<style scoped>
.axo-timebar { display: flex; height: 12px; border-radius: 6px; overflow: hidden; gap: 2px; }
.axo-timebar-part { min-width: 4px; flex-basis: 0; }
.axo-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
</style>
