<template>
  <div>
    <v-breadcrumbs :items="[{ title: 'Functions', to: `/ves/${id}/functions` }, { title: row?.name ?? '…', disabled: true }]"
                   density="compact" class="px-0 pt-0 mb-1" />
    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: the function shows here once it runs.
    </v-alert>
    <AxoErrorAlert v-else-if="functions.error.value" :error="functions.error.value" title="The function couldn't load"
                   :retry="() => functions.load(true)" class="mb-4" />
    <AxoEmptyState v-else-if="loaded && !row" icon="mdi-function-variant" title="This function is not in the VE"
                   text="It may have been removed for good." />
    <AxoSkeleton v-else-if="!row" :lines="8" />

    <template v-if="row && latest">
      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <v-icon color="primary">mdi-function-variant</v-icon>
        <span class="text-h6 text-truncate">{{ row.name }}</span>
        <span class="text-body-2 axo-muted">v{{ latest.version }}</span>
        <AxoStatusChip kind="function" :value="functionState(latest, row.deleted)" />
        <v-spacer />
        <v-btn v-if="can('developer')" variant="tonal" class="text-none" prepend-icon="mdi-source-branch-plus"
               :to="`/ves/${id}/functions/new?function=${functionId}`">New version</v-btn>
        <AxoBtn v-if="can('developer') && !row.deleted" prepend-icon="mdi-play" :to="`/ves/${id}/functions/${functionId}/run`">Run</AxoBtn>
      </div>
      <div class="text-body-2 axo-muted mb-3">
        {{ latest.description || 'No description (the first line of its docstring shows here).' }}
        · {{ runtimeLabel(latest.runtime_spec) }}
      </div>

      <v-tabs :model-value="tab" color="primary" class="mb-5 axo-fn-tabs" show-arrows>
        <v-tab v-for="t in tabs" :key="t.path" :value="t.path" :to="`/ves/${id}/functions/${functionId}${t.path ? `/${t.path}` : ''}`"
               :exact="!t.path" :prepend-icon="t.icon">{{ t.title }}</v-tab>
      </v-tabs>
      <NuxtPage />
    </template>
  </div>
</template>

<script setup lang="ts">
// The frame of one function's pages: its name, state and tabs (Overview ·
// Run · Jobs · Code · Versions · Settings · Activity).
const { id, ve, can, vemUp } = useCurrentVe()
const { functionId, row, latest, functions } = useCurrentFunction()
useHead(() => ({ title: `${row.value?.name ?? 'Function'} · ${ve.value?.name ?? 'VE'} · axo` }))
const loaded = ref(false)
async function load() { await functions.load(true); loaded.value = true }
watch([functionId, vemUp], load)
usePolling(load, 20_000)

const tabs = computed(() => [
  { path: '', title: 'Overview', icon: 'mdi-view-grid-outline' },
  { path: 'run', title: 'Run', icon: 'mdi-play-outline' },
  { path: 'jobs', title: 'Jobs', icon: 'mdi-briefcase-outline' },
  { path: 'code', title: 'Code', icon: 'mdi-code-tags' },
  { path: 'versions', title: 'Versions', icon: 'mdi-source-branch' },
  ...(can('developer') ? [{ path: 'settings', title: 'Settings', icon: 'mdi-tune-variant' }] : []),
  { path: 'activity', title: 'Activity', icon: 'mdi-timeline-text-outline' },
])
const route = useRoute()
const tab = computed(() => route.path.split('/')[5] ?? '')
</script>

<style scoped>
.axo-fn-tabs { border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
</style>
