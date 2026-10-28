<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <div class="text-h6 d-flex align-center ga-1">Jobs
          <AxoHelp title="Jobs" text="Every run of a function or an object's method in this VE, newest first. The jobs of one choreography run are grouped under the run. A job keeps one id; each try of it is an attempt." />
        </div>
        <div class="text-body-2 axo-muted">Open one for its attempts, result and files.</div>
      </div>
    </div>
    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: its jobs show here once it runs (a sleeping VE wakes with its next job).
    </v-alert>
    <JobsList v-else />
  </div>
</template>

<script setup lang="ts">
// The VE's jobs (every function, every way they were started).
const { ve, vemUp } = useCurrentVe()
useHead(() => ({ title: `Jobs · ${ve.value?.name ?? 'VE'} · axo` }))
</script>
