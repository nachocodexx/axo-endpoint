<template>
  <RunForm v-if="versions.length" :key="functionId" :versions="versions" :initial-version="initialVersion" :from-job="fromJob" />
</template>

<script setup lang="ts">
// The Run tab: `?version=` picks the version, `?from=<job id>` fills the
// form with that job's values (Run again).
const { functionId, versions } = useCurrentFunction()
const route = useRoute()
const initialVersion = computed(() => (route.query.version ? Number(route.query.version) : undefined))
const fromJob = computed(() => (route.query.from as string) || undefined)
</script>
