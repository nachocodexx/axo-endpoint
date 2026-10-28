<template>
  <v-btn-toggle v-model="model" mandatory density="comfortable" variant="outlined" divided color="primary" rounded="lg">
    <v-btn v-for="r in RANGE_PRESETS" :key="r.value" :value="r.value" size="small">{{ r.value }}</v-btn>
  </v-btn-toggle>
</template>

<script setup lang="ts">
import { RANGE_PRESETS, type RangeValue } from '../composables/useTimeRange'

// 1h / 24h / 7d / 30d.
const model = defineModel<RangeValue>({ required: true })
</script>
