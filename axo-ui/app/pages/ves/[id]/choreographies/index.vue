<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <div class="text-h6 d-flex align-center ga-1">Choreographies
          <AxoHelp title="Choreographies" text="A choreography joins functions, objects and buckets into steps: each step starts when the ones before it finish, and gets their answers. Runs start by hand, from Python, when a new file arrives in a bucket, or when someone sends an event. Every change is a new version." />
        </div>
        <div class="text-body-2 axo-muted">{{ rows.length }} choreograph{{ rows.length === 1 ? 'y' : 'ies' }}{{ running ? ` · ${running} running now` : '' }}</div>
      </div>
      <v-spacer />
      <AxoBtn v-if="can('developer')" prepend-icon="mdi-plus" :disabled="!vemUp" :to="`/ves/${id}/choreographies/new`">New choreography</AxoBtn>
    </div>

    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: its choreographies show here once it runs.
    </v-alert>
    <AxoErrorAlert v-if="error" :error="error" title="The choreographies couldn't load" :retry="() => load(true)" class="mb-4" />

    <template v-if="vemUp">
      <AxoEmptyState v-if="loaded && !rows.length" icon="mdi-sitemap-outline" title="No choreographies yet"
                     :text="can('developer') ? 'Draw one with New choreography, or write one in Python.' : 'Developers make them, here or in Python.'">
        <NuxtLink to="/help/choreographies" class="text-label-large axo-link d-inline-block mt-1">See the guide</NuxtLink>
        <br/>
        <AxoBtn v-if="can('developer')" class="mt-3" prepend-icon="mdi-plus" :to="`/ves/${id}/choreographies/new`">New choreography</AxoBtn>
      </AxoEmptyState>

      <template v-else>
        <div class="d-flex align-center flex-wrap ga-2 mb-3">
          <v-text-field v-model="search" density="compact" variant="outlined" hide-details clearable prepend-inner-icon="mdi-magnify"
                        placeholder="Name or description" class="axo-filter" />
          <v-chip-group v-model="onlyTriggers" column>
            <v-chip :value="true" filter variant="outlined" size="small" prepend-icon="mdi-lightning-bolt">Has triggers</v-chip>
          </v-chip-group>
        </div>
        <AxoDataTable :headers="headers" :items="shown" item-value="choreography_id" :searchable="false" :loading="loading && !rows.length"
                      :row-to="(r) => `/ves/${id}/choreographies/${encodeURIComponent(r.name)}`" :sort-by="[{ key: 'name', order: 'asc' }]"
                      empty-icon="mdi-sitemap-outline" empty-title="No choreography matches" empty-text="Change the filters.">
          <template #item.name="{ item }">
            <div class="py-2" style="max-width: 360px">
              <div class="text-body-2 font-weight-medium">{{ item.name }}</div>
              <div v-if="item.description" class="text-caption axo-muted text-truncate">{{ item.description }}</div>
            </div>
          </template>
          <template #item.version="{ item }"><span class="text-body-2">v{{ item.version }}</span></template>
          <template #item.steps="{ item }"><span class="text-body-2">{{ item.steps }}</span></template>
          <template #item.triggers="{ item }">
            <span v-if="item.triggers" class="text-body-2 text-no-wrap"><v-icon icon="mdi-lightning-bolt" size="14" color="warning" /> {{ item.triggers }}</span>
            <span v-else class="axo-muted">—</span>
          </template>
          <template #item.runs_24h="{ item }">
            <span class="text-body-2 text-no-wrap">{{ formatNumber(item.runs_24h) }}</span>
            <span v-if="item.errors_24h" class="text-caption text-error text-no-wrap"> · {{ item.errors_24h }} with errors</span>
          </template>
          <template #item.last_run="{ item }">
            <NuxtLink v-if="item.last_run" :to="`/ves/${id}/choreographies/${encodeURIComponent(item.name)}/runs/${item.last_run.run_id}`"
                      class="d-inline-flex align-center ga-1 text-no-wrap axo-link" @click.stop>
              <AxoStatusChip kind="run" :value="item.last_run.status" size="x-small" />
              <span class="text-caption axo-muted">{{ timeAgo(ts(item.last_run.finished_at ?? item.last_run.queued_at)) }}</span>
            </NuxtLink>
            <span v-else class="axo-muted">—</span>
          </template>
          <template #item.active_runs="{ item }">
            <span v-if="item.active_runs" class="text-body-2 text-info text-no-wrap"><AxoRunningRing :size="11" class="me-1" />{{ item.active_runs }}</span>
            <span v-else class="axo-muted">—</span>
          </template>
          <template #item.updated_at="{ item }">
            <span class="text-body-2 text-no-wrap" :title="formatExactTime(ts(item.updated_at))">{{ timeAgo(ts(item.updated_at)) }}</span>
            <div v-if="item.updated_by_name" class="text-caption axo-muted">{{ item.updated_by_name }}</div>
          </template>
          <template #item.actions="{ item }">
            <div class="d-flex justify-end align-center ga-1" @click.stop>
              <v-btn v-if="can('developer')" size="small" variant="tonal" color="primary" class="text-none" prepend-icon="mdi-play" @click="openRun(item.name)">Run…</v-btn>
              <v-menu location="bottom end">
                <template #activator="{ props: menu }">
                  <v-btn v-bind="menu" icon="mdi-dots-vertical" size="small" variant="text" :aria-label="`More for ${item.name}`" />
                </template>
                <v-list density="compact">
                  <v-list-item prepend-icon="mdi-sitemap-outline" :title="can('developer') ? 'Open the designer' : 'View the designer'"
                               :to="`/ves/${id}/choreographies/${encodeURIComponent(item.name)}/designer`" />
                  <v-list-item v-if="can('developer')" prepend-icon="mdi-content-copy" title="Duplicate" @click="duplicate(item.name)" />
                  <v-list-item v-if="can('admin')" prepend-icon="mdi-delete-outline" base-color="error" title="Delete…" @click="remove(item)" />
                </v-list>
              </v-menu>
            </div>
          </template>
        </AxoDataTable>
      </template>
    </template>

    <ChoreographyRunDialog v-model="runOpen" :doc="runDoc" :versions="runVersions" />
  </div>
</template>

<script setup lang="ts">
// The VE's choreographies: each one's version, steps, triggers, runs in the
// last 24 h (and how many had errors), its last run, runs going now; Run…,
// open the designer, duplicate, delete (Admin). Live: the runs feed.
import type { Choreography, ChoreographyRow } from '~/utils/choreographies'
import type { VersionRow } from '~/composables/useCurrentChoreography'

const { id, ve, can, vemUp, api } = useCurrentVe()
useHead(() => ({ title: `Choreographies · ${ve.value?.name ?? 'VE'} · axo` }))
const { rows, loading, error, load } = useChoreographies()
const notifier = useNotifier()
const { confirm } = useConfirm()
const route = useRoute()
const router = useRouter()

const search = ref((route.query.q as string) ?? '')
const onlyTriggers = ref<boolean | null>(route.query.triggers === '1' ? true : null)
watch([search, onlyTriggers], () => router.replace({ query: { ...route.query, q: search.value || undefined, triggers: onlyTriggers.value ? '1' : undefined } }))
const shown = computed(() => rows.value.filter((r) => {
  const q = (search.value ?? '').trim().toLowerCase()
  return (!q || r.name.toLowerCase().includes(q) || (r.description ?? '').toLowerCase().includes(q)) && (!onlyTriggers.value || r.triggers > 0)
}))
const running = computed(() => rows.value.reduce((n, r) => n + (r.active_runs || 0), 0))

const WIDE = { headerProps: { class: 'd-none d-md-table-cell' }, cellProps: { class: 'd-none d-md-table-cell' } }
const headers = [
  { title: 'Name', key: 'name' },
  { title: 'Version', key: 'version', ...WIDE },
  { title: 'Steps', key: 'steps', ...WIDE },
  { title: 'Triggers', key: 'triggers', ...WIDE },
  { title: 'Runs (24 h)', key: 'runs_24h' },
  { title: 'Last run', key: 'last_run', sortable: false },
  { title: 'Running', key: 'active_runs', ...WIDE },
  { title: 'Edited', key: 'updated_at', ...WIDE },
  { title: '', key: 'actions', sortable: false, align: 'end' as const },
]

const loaded = ref(false)
async function refresh() { await load(true); loaded.value = true }
onMounted(refresh)
watch(vemUp, refresh)
usePolling(() => load(true), 20_000)
let liveTimer: ReturnType<typeof setTimeout> | undefined
useVeLive(() => (vemUp.value ? 'choreographies' : null), () => { clearTimeout(liveTimer); liveTimer = setTimeout(() => load(true), 800) })

// Run…
const runOpen = ref(false)
const runDoc = ref<Choreography | null>(null)
const runVersions = ref<VersionRow[]>([])
async function openRun(name: string) {
  try {
    const enc = encodeURIComponent(name)
    ;[runDoc.value, runVersions.value] = await Promise.all([api<Choreography>(`/choreographies/${enc}`), api<VersionRow[]>(`/choreographies/${enc}/versions`)])
    runOpen.value = true
  } catch (err) { notifier.error(err, `${name} couldn't load`) }
}
async function duplicate(name: string) {
  try {
    const doc = await api<Choreography>(`/choreographies/${encodeURIComponent(name)}`)
    const taken = new Set(rows.value.map(r => r.name))
    let copy = `${name}-copy`
    for (let i = 2; taken.has(copy); i++) copy = `${name}-copy-${i}`
    const saved = await api<Choreography>('/choreographies', { method: 'POST',
      body: { name: copy, description: doc.description, graph: cleanGraph({ ...doc.graph, triggers: [] }), settings: doc.settings } })
    notifier.success(`${saved.name} made (without the triggers)`, { to: `/ves/${id.value}/choreographies/${encodeURIComponent(saved.name)}/designer` })
    await load(true)
  } catch (err) { notifier.error(err, `${name} couldn't be duplicated`) }
}
async function remove(r: ChoreographyRow) {
  const ok = await confirm({ title: `Delete ${r.name}?`, danger: true, typeToConfirm: r.name, confirmText: 'Delete',
    consequences: [r.active_runs ? `Its ${r.active_runs} waiting and running run${r.active_runs === 1 ? ' is' : 's are'} cancelled.` : 'No run is going.',
      r.triggers ? `Its ${r.triggers} trigger${r.triggers === 1 ? '' : 's'} stop starting runs.` : 'It has no triggers.',
      'Past runs stay in the history until the VE\'s retention ends.'] })
  if (!ok) return
  try {
    await api(`/choreographies/${encodeURIComponent(r.choreography_id)}`, { method: 'DELETE' })
    notifier.success(`${r.name} deleted`)
    await load(true)
  } catch (err) { notifier.error(err, `${r.name} couldn't be deleted`) }
}
</script>
