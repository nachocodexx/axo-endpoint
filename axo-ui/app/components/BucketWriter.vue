<template>
  <span v-if="!by" class="axo-muted">—</span>
  <span v-else-if="by.kind === 'job'" class="d-inline-flex align-center ga-1">
    <v-icon size="16" class="axo-muted">mdi-function-variant</v-icon>
    <NuxtLink v-if="jobLink" :to="jobLink" class="axo-link" @click.stop>{{ jobText }}</NuxtLink>
    <span v-else>{{ jobText }}</span>
    <span v-if="by.user_name" class="text-caption axo-muted">for {{ by.user_name }}</span>
  </span>
  <span v-else-if="by.kind === 'choreography'" class="d-inline-flex align-center ga-1">
    <v-icon size="16" class="axo-muted">mdi-graph-outline</v-icon> A choreography
    <span v-if="by.user_name" class="text-caption axo-muted">for {{ by.user_name }}</span>
  </span>
  <span v-else class="d-inline-flex align-center ga-1">
    <v-icon size="16" class="axo-muted">mdi-account-outline</v-icon>{{ by.user_name ?? 'Someone' }}
  </span>
</template>

<script setup lang="ts">
import type { Writer } from '../utils/buckets'

// Who wrote a file: a person (an upload from the page), a job -- shown as its
// function (or choreography step) with a link to the job when you have a
// role in its VE -- or a choreography's bucket step.
const props = defineProps<{ by?: Writer | null; functionName?: (veId: string, functionId: string) => string | undefined }>()
const ves = useVesStore()

const jobLink = computed(() => {
  const b = props.by
  return b?.kind === 'job' && b.ve_id && ves.byId(b.ve_id) ? `/ves/${b.ve_id}/jobs/${b.job_id}` : null
})
const jobText = computed(() => {
  const b = props.by!
  const fn = b.ve_id && b.function_id ? props.functionName?.(b.ve_id, b.function_id) ?? null : null
  if (b.choreography_step) return `Step ${b.choreography_step}${fn ? ` (${fn})` : ''}`
  if (b.object_method) return `${b.function_id ?? 'Object'}.${b.object_method.replace(/^__(.*)__$/, '$1')}`
  if (fn) return `${fn}${b.function_version ? ` v${b.function_version}` : ''}`
  return 'A job'
})
</script>
