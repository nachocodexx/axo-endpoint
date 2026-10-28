<template>
  <div>
    <v-breadcrumbs :items="[{ title: 'Choreographies', to: `/ves/${id}/choreographies` }, { title: name, disabled: true }]"
                   density="compact" class="px-0 pt-0 mb-1" />
    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: the choreography shows here once it runs.
    </v-alert>
    <AxoErrorAlert v-else-if="error" :error="error" title="The choreography couldn't load" :retry="load" class="mb-4" />
    <AxoEmptyState v-else-if="missing" icon="mdi-sitemap-outline" title="No choreography with this name" text="It may have been deleted or renamed." />
    <AxoSkeleton v-else-if="!doc" :lines="8" />

    <template v-if="doc">
      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <v-icon color="primary">mdi-sitemap-outline</v-icon>
        <span class="text-h6 text-truncate">{{ doc.name }}</span>
        <span class="text-body-2 axo-muted">v{{ doc.version }}</span>
        <v-chip v-if="doc.active_runs" size="small" color="info" variant="tonal"><AxoRunningRing :size="12" class="me-2" />{{ doc.active_runs }} running</v-chip>
        <v-spacer />
        <template v-if="can('developer')">
          <v-btn variant="tonal" class="text-none" prepend-icon="mdi-pencil-outline" :to="`${base}/designer`">Edit</v-btn>
          <AxoBtn prepend-icon="mdi-play" @click="runOpen = true">Run…</AxoBtn>
          <v-menu location="bottom end">
            <template #activator="{ props: menu }"><v-btn v-bind="menu" icon="mdi-dots-vertical" variant="text" aria-label="More" /></template>
            <v-list density="compact">
              <v-list-item prepend-icon="mdi-content-copy" title="Duplicate" @click="duplicate" />
              <v-list-item v-if="can('admin')" prepend-icon="mdi-delete-outline" base-color="error" title="Delete…" @click="remove" />
            </v-list>
          </v-menu>
        </template>
      </div>
      <div class="text-body-2 axo-muted mb-3">
        {{ doc.description || 'No description.' }}
        · edited {{ timeAgo(ts(doc.updated_at)) }}{{ doc.updated_by_name ? ` by ${doc.updated_by_name}` : '' }}
      </div>

      <v-tabs :model-value="tab" color="primary" class="mb-5 axo-ch-tabs" show-arrows>
        <v-tab v-for="t in tabs" :key="t.path" :value="t.path" :to="`${base}${t.path ? `/${t.path}` : ''}`" :exact="!t.path" :prepend-icon="t.icon">{{ t.title }}</v-tab>
      </v-tabs>
      <NuxtPage />
      <ChoreographyRunDialog v-model="runOpen" :doc="doc" :versions="versions" />
    </template>
  </div>
</template>

<script setup lang="ts">
// The frame of one choreography's pages: name, version, runs going, Run… /
// Edit / Duplicate / Delete, and tabs (Overview · Designer · Runs ·
// Versions · Triggers · Settings · Activity · Costs).
import type { Choreography } from '~/utils/choreographies'

const { id, ve, can, vemUp, api } = useCurrentVe()
const { name, doc, versions, missing, error, load } = useCurrentChoreography()
const { load: loadList } = useChoreographies()
const notifier = useNotifier()
const { confirm } = useConfirm()
useHead(() => ({ title: `${name.value} · ${ve.value?.name ?? 'VE'} · axo` }))
const base = computed(() => `/ves/${id.value}/choreographies/${encodeURIComponent(name.value)}`)
watch([name, vemUp], load, { immediate: true })
usePolling(load, 30_000)
let liveTimer: ReturnType<typeof setTimeout> | undefined
useVeLive(() => (vemUp.value ? 'choreographies' : null), (m: any) => {
  if (m?.choreography_id && m.choreography_id !== doc.value?.choreography_id) return
  clearTimeout(liveTimer); liveTimer = setTimeout(load, 800)
})

const tabs = computed(() => [
  { path: '', title: 'Overview', icon: 'mdi-view-grid-outline' },
  { path: 'designer', title: 'Designer', icon: 'mdi-sitemap-outline' },
  { path: 'runs', title: 'Runs', icon: 'mdi-play-circle-outline' },
  { path: 'versions', title: 'Versions', icon: 'mdi-source-branch' },
  { path: 'triggers', title: 'Triggers', icon: 'mdi-lightning-bolt-outline' },
  { path: 'settings', title: 'Settings', icon: 'mdi-tune-variant' },
  { path: 'activity', title: 'Activity', icon: 'mdi-timeline-text-outline' },
  ...(can('admin') ? [{ path: 'costs', title: 'Costs', icon: 'mdi-cash-multiple' }] : []),
])
const route = useRoute()
const tab = computed(() => route.path.split('/')[5] ?? '')
const runOpen = ref(false)
watch(() => route.query.run, (v) => { if (v) runOpen.value = true }, { immediate: true })

async function duplicate() {
  if (!doc.value) return
  try {
    let copy = `${doc.value.name}-copy`
    const taken = new Set((await api<Array<{ name: string }>>('/choreographies')).map(r => r.name))
    for (let i = 2; taken.has(copy); i++) copy = `${doc.value.name}-copy-${i}`
    const saved = await api<Choreography>('/choreographies', { method: 'POST',
      body: { name: copy, description: doc.value.description, graph: cleanGraph({ ...doc.value.graph, triggers: [] }), settings: doc.value.settings } })
    await loadList(true)
    notifier.success(`${saved.name} made (without the triggers)`)
    await navigateTo(`/ves/${id.value}/choreographies/${encodeURIComponent(saved.name)}/designer`)
  } catch (err) { notifier.error(err, "It couldn't be duplicated") }
}
async function remove() {
  const d = doc.value
  if (!d) return
  const ok = await confirm({ title: `Delete ${d.name}?`, danger: true, typeToConfirm: d.name, confirmText: 'Delete',
    consequences: [d.active_runs ? `Its ${d.active_runs} waiting and running runs are cancelled.` : 'No run is going.',
      d.triggers.length ? `Its ${d.triggers.length} trigger${d.triggers.length === 1 ? '' : 's'} stop starting runs.` : 'It has no triggers.',
      "Past runs stay in the history until the VE's retention ends."] })
  if (!ok) return
  try {
    await api(`/choreographies/${encodeURIComponent(d.choreography_id)}`, { method: 'DELETE' })
    await loadList(true)
    notifier.success(`${d.name} deleted`)
    await navigateTo(`/ves/${id.value}/choreographies`)
  } catch (err) { notifier.error(err, `${d.name} couldn't be deleted`) }
}
</script>

<style scoped>
.axo-ch-tabs { border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
</style>
