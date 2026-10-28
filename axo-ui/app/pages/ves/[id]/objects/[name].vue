<template>
  <div>
    <v-breadcrumbs :items="[{ title: 'Objects', to: `/ves/${id}/objects` }, { title: name, disabled: true }]" density="compact" class="px-0 pt-0 mb-1" />
    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: the object shows here once it runs.
    </v-alert>
    <AxoErrorAlert v-else-if="store.error.value" :error="store.error.value" title="The object couldn't load" :retry="load" class="mb-4" />
    <AxoEmptyState v-else-if="loaded && !object" icon="mdi-shape-outline" title="This object is not in the VE"
                   text="It may have been deleted, or its __init__ failed." />
    <AxoSkeleton v-else-if="!object" :lines="8" />

    <template v-if="object">
      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <v-icon color="primary">mdi-shape-outline</v-icon>
        <span class="text-h6 axo-mono text-truncate">{{ object.name }}</span>
        <AxoStatusChip kind="object" :value="object.status" />
        <v-spacer />
        <template v-if="can('developer')">
          <v-menu location="bottom end">
            <template #activator="{ props: menu }">
              <v-btn v-bind="menu" variant="tonal" class="text-none" append-icon="mdi-chevron-down">More</v-btn>
            </template>
            <v-list density="compact">
              <v-list-item prepend-icon="mdi-content-copy" title="Clone…" @click="openName('clone')" />
              <v-list-item prepend-icon="mdi-arrow-up-bold-circle-outline" title="Upgrade…" :disabled="newest <= object.class_version" @click="openName('upgrade')" />
              <v-list-item v-if="can('admin')" prepend-icon="mdi-delete-outline" title="Delete…" base-color="error" @click="remove" />
            </v-list>
          </v-menu>
          <AxoBtn prepend-icon="mdi-play" :disabled="object.status !== 'ready'" @click="openCall(null)">Call…</AxoBtn>
        </template>
      </div>
      <div class="text-body-2 axo-muted mb-3">
        <NuxtLink :to="`/ves/${id}/objects/classes/${encodeURIComponent(object.class_name)}`" class="axo-link">{{ object.class_name }}</NuxtLink>
        v{{ object.class_version }}
        <template v-if="newest > object.class_version">
          · <a href="#" class="axo-link" @click.prevent="openName('upgrade')">v{{ newest }} available</a>
        </template>
        · revision {{ object.revision }} · {{ formatBytes(object.size_bytes) }}
        <template v-if="object.cloned_from"> · cloned from
          <NuxtLink :to="`/ves/${id}/objects/${encodeURIComponent(object.cloned_from)}`" class="axo-link axo-mono">{{ object.cloned_from }}</NuxtLink>
        </template>
      </div>

      <v-tabs v-model="tab" color="primary" class="mb-5 axo-tabs" show-arrows>
        <v-tab value="overview" prepend-icon="mdi-view-grid-outline">Overview</v-tab>
        <v-tab value="calls" prepend-icon="mdi-history">Calls</v-tab>
        <v-tab value="revisions" prepend-icon="mdi-source-commit">Revisions</v-tab>
        <v-tab v-if="can('developer')" value="settings" prepend-icon="mdi-tune-variant">Settings</v-tab>
      </v-tabs>

      <!-- ── Overview ── -->
      <v-row v-if="tab === 'overview'" density="compact">
        <v-col cols="12" lg="8">
          <AxoPanel title="What it holds" icon="mdi-cube-scan" stacked flush
                    help="Its public attributes as saved after the last call. Small JSON values are shown in full; big values (and bytes or Python objects) by kind and size.">
            <template #actions>
              <v-btn size="small" variant="text" icon="mdi-refresh" aria-label="Read again" :loading="stateLoading" @click="loadState()" />
            </template>
            <AxoErrorAlert v-if="stateError" :error="stateError" title="What it holds couldn't load" :retry="() => loadState()" class="ma-4" />
            <AttributeTable v-else-if="state" :attributes="state.attributes" />
            <div v-else-if="object.status !== 'ready'" class="pa-4 text-body-2 axo-muted">Its __init__ is running: the values show when it's done.</div>
            <AxoSkeleton v-else :lines="4" class="pa-4" />
          </AxoPanel>
        </v-col>
        <v-col cols="12" lg="4">
          <AxoPanel title="Details" icon="mdi-information-outline" stacked>
            <AxoKeyValue :items="facts" :columns="2" />
          </AxoPanel>
        </v-col>
      </v-row>

      <!-- ── Calls ── -->
      <div v-else-if="tab === 'calls'">
        <div class="d-flex align-center flex-wrap ga-2 mb-3">
          <v-btn-toggle v-model="callFilter" density="compact" variant="outlined" mandatory divided color="primary">
            <v-btn value="all" class="text-none" size="small">All</v-btn>
            <v-btn value="calls" class="text-none" size="small">Calls</v-btn>
            <v-btn value="failed" class="text-none" size="small">Failed</v-btn>
          </v-btn-toggle>
          <v-spacer />
          <span class="text-caption axo-muted">Created, every call, upgrades, restores and clones — newest first.</span>
        </div>
        <AxoErrorAlert v-if="feed.error.value" :error="feed.error.value" title="The history couldn't load" :retry="feed.load" class="mb-3" />
        <v-card variant="outlined">
          <v-list v-if="shownRows.length" density="compact" class="py-0">
            <template v-for="(row, i) in shownRows" :key="row.event_id">
              <v-divider v-if="i" />
              <v-list-item class="py-2">
                <template #prepend>
                  <v-icon :color="rowColor(row)" size="20" class="mr-1">{{ rowIcon(row) }}</v-icon>
                </template>
                <v-list-item-title class="text-body-2 d-flex align-center flex-wrap ga-2">
                  <span class="font-weight-medium" :class="{ 'axo-mono': isCall(row) }">{{ objectEventTitle(row) }}</span>
                  <AxoStatusChip v-if="row.event_type === 'ActiveObjectCalled' && row.meta.outcome !== 'completed'" kind="job" :value="row.meta.outcome" size="x-small" />
                  <span v-if="row.meta.revision != null && row.meta.outcome !== 'failed_user'" class="text-caption axo-muted">revision {{ row.meta.revision }}</span>
                </v-list-item-title>
                <v-list-item-subtitle class="text-caption">
                  <span :title="formatExactTime(ts(row.created_at))">{{ timeAgo(ts(row.created_at)) }}</span>
                  <template v-if="row.user_id"> · {{ row.user_id }}</template>
                  <template v-if="row.meta.duration_ms != null"> · {{ formatMs(row.meta.duration_ms) }}</template>
                  <template v-if="row.meta.changed?.length && row.meta.outcome === 'completed'"> · changed <span class="axo-mono">{{ row.meta.changed.join(', ') }}</span></template>
                  <template v-if="row.meta.failure?.message"> · <span class="text-error">{{ row.meta.failure.message }}</span></template>
                </v-list-item-subtitle>
                <template #append>
                  <v-btn v-if="row.meta.job_id && !String(row.meta.job_id).startsWith('restore-')" size="small" variant="text" class="text-none"
                         :to="`/ves/${id}/jobs/${row.meta.job_id}`">Job</v-btn>
                </template>
              </v-list-item>
            </template>
          </v-list>
          <div v-else-if="feed.loading.value" class="pa-4"><AxoSkeleton :lines="4" /></div>
          <div v-else class="pa-4 text-body-2 axo-muted">{{ callFilter === 'failed' ? 'No failed calls in what is loaded.' : 'Nothing yet.' }}</div>
        </v-card>
        <div v-if="feed.more.value" class="d-flex justify-center mt-3">
          <v-btn variant="tonal" class="text-none" :loading="feed.loadingMore.value" @click="feed.loadMore">Load more</v-btn>
        </div>
      </div>

      <!-- ── Revisions ── -->
      <v-row v-else-if="tab === 'revisions'" density="compact">
        <v-col cols="12" lg="5">
          <div class="text-body-2 axo-muted mb-3">
            Every call that changes something saves a new revision. The object keeps the last {{ keepNow }} past ones;
            restoring one saves it again as a new revision (nothing is lost).
          </div>
          <v-card variant="outlined">
            <v-list v-if="state" density="compact" class="py-0" :selected="[viewing]" color="primary" mandatory>
              <template v-for="(r, i) in state.revisions" :key="r.revision">
                <v-divider v-if="i" />
                <v-list-item :value="r.revision" class="py-2" @click="view(r.revision)">
                  <v-list-item-title class="text-body-2 d-flex align-center ga-2">
                    <span class="font-weight-medium">Revision {{ r.revision }}</span>
                    <v-chip v-if="r.revision === state.revision" size="x-small" color="primary" variant="flat">now</v-chip>
                  </v-list-item-title>
                  <v-list-item-subtitle class="text-caption">
                    {{ r.updated_at ? timeAgo(atSeconds(r.updated_at)) : '—' }} · {{ formatBytes(r.size_bytes) }}
                    <template v-if="madeBy(r.revision)"> · {{ madeBy(r.revision)!.text }}</template>
                  </v-list-item-subtitle>
                  <template #append>
                    <v-btn v-if="madeBy(r.revision)?.job" size="small" variant="text" class="text-none" :to="`/ves/${id}/jobs/${madeBy(r.revision)!.job}`" @click.stop>Job</v-btn>
                  </template>
                </v-list-item>
              </template>
            </v-list>
            <AxoSkeleton v-else :lines="4" class="pa-4" />
          </v-card>
        </v-col>
        <v-col cols="12" lg="7">
          <AxoPanel :title="viewing === state?.revision ? `Revision ${viewing} (now)` : `Revision ${viewing}`" icon="mdi-source-commit" stacked flush>
            <template #actions>
              <AxoBtn v-if="can('developer') && state && viewing !== state.revision" size="small" prepend-icon="mdi-restore"
                      :disabled="state.busy" :loading="restoring" @click="restore">Restore</AxoBtn>
            </template>
            <div v-if="state?.busy && viewing !== state?.revision" class="px-4 pt-3 text-caption text-warning">A call is queued or running: restore when it's done.</div>
            <AxoErrorAlert v-if="pastError" :error="pastError" title="This revision couldn't load" class="ma-4" />
            <AttributeTable v-else-if="pastState" :attributes="pastState.attributes" :compare="viewing !== state?.revision ? state?.attributes : undefined" />
            <AxoSkeleton v-else :lines="4" class="pa-4" />
          </AxoPanel>
        </v-col>
      </v-row>

      <!-- ── Settings ── -->
      <v-row v-else-if="tab === 'settings'" density="compact">
        <v-col cols="12" lg="7">
          <AxoPanel title="Past revisions" icon="mdi-source-commit" stacked class="mb-3">
            <v-text-field v-model.number="keepInput" type="number" min="0" :max="maxKeep ?? undefined" label="Past revisions to keep"
                          density="compact" variant="outlined" :placeholder="`${planKeep} (the plan's default)`" persistent-placeholder
                          :error-messages="keepError" :hint="maxKeep != null ? `Up to ${maxKeep} on the ${ve?.plan_id ?? ''} plan. Empty = the plan's default.` : 'Empty = the plan’s default.'"
                          persistent-hint clearable />
            <v-alert v-if="lowering" type="warning" variant="tonal" density="compact" class="mt-3">
              Lowering it drops the oldest past revisions at the next save: {{ (state?.revisions.length ?? 1) - 1 - (keepInput ?? 0) }} can't be restored after that.
            </v-alert>
            <div class="d-flex mt-3">
              <v-spacer />
              <AxoBtn :loading="saving" :disabled="!!keepError || keepInput === (object.keep_revisions ?? null)" @click="saveKeep">Save</AxoBtn>
            </div>
          </AxoPanel>
          <AxoPanel v-if="can('admin')" title="Danger zone" icon="mdi-alert-outline" stacked>
            <div class="d-flex align-center flex-wrap ga-3">
              <div class="text-body-2 flex-grow-1">Delete the object and every kept revision, for good.</div>
              <v-btn color="error" variant="tonal" class="text-none" prepend-icon="mdi-delete-outline" @click="remove">Delete…</v-btn>
            </div>
          </AxoPanel>
        </v-col>
        <v-col cols="12" lg="5">
          <AxoPanel title="Plan limits" icon="mdi-gauge" stacked help="Set by the VE's plan; a call that would go over the state size fails and saves nothing.">
            <AxoKeyValue :items="limitFacts" :columns="1" />
            <div class="text-caption axo-muted mt-3">What objects keep is on the VE's bill as Object storage (see Costs).</div>
          </AxoPanel>
        </v-col>
      </v-row>
    </template>

    <ObjectCallDialog v-if="object" v-model="callOpen" :object-name="name" :method="callMethod" @called="afterChange" />
    <ObjectNameDialog v-if="object" v-model="nameOpen" :object-name="name" :mode="nameMode" @done="afterChange" />
  </div>
</template>

<script setup lang="ts">
import type { ObjectState } from '~/utils/objects'
import type { ActivityRow } from '~/utils/activity'

// One object: Overview (what it holds + details), Calls (its history),
// Revisions (each kept one; view it against now; Restore = a new revision)
// and Settings (past revisions to keep, plan limits, delete). Live on the
// VEM's "objects/<object id>" feed.
const { id, ve, can, vemUp, api } = useCurrentVe()
const store = useObjects()
const actions = useObjectActions()
const notifier = useNotifier()
const route = useRoute()
const router = useRouter()
const name = computed(() => String(route.params.name ?? ''))
const object = computed(() => store.objectOf(name.value))
const newest = computed(() => store.classOf(object.value?.class_name ?? '')?.latest.version ?? object.value?.class_version ?? 0)
useHead(() => ({ title: `${name.value} · Objects · ${ve.value?.name ?? 'VE'} · axo` }))

const TABS = ['overview', 'calls', 'revisions', 'settings']
const tab = ref(TABS.includes(route.query.tab as string) ? (route.query.tab as string) : 'overview')
watch(tab, t => router.replace({ query: { ...route.query, tab: t === 'overview' ? undefined : t } }))

// ── state ──────────────────────────────────────────────────────────────────
const state = ref<ObjectState | null>(null)
const stateError = ref<unknown>(null)
const stateLoading = ref(false)
async function loadState() {
  if (!object.value || object.value.status !== 'ready') return
  stateLoading.value = true
  try {
    state.value = await api<ObjectState>(`/objects/${encodeURIComponent(name.value)}/state`)
    stateError.value = null
    if (viewing.value === 0 || viewing.value === undefined) viewing.value = state.value.revision
  } catch (err) {
    stateError.value = err
  } finally {
    stateLoading.value = false
  }
}

const facts = computed(() => {
  const o = object.value!
  return [
    { label: 'Class', value: `${o.class_name} v${o.class_version}`, to: `/ves/${id.value}/objects/classes/${encodeURIComponent(o.class_name)}` },
    { label: 'Revision', value: String(o.revision) },
    { label: 'Size', value: formatBytes(o.size_bytes) },
    { label: 'Calls', value: formatNumber(o.calls) },
    { label: 'Last call', value: o.last_call_at ? `${objectMethodTitle(o.last_method)} · ${timeAgo(ts(o.last_call_at))}` : '—' },
    { label: 'Created', value: o.created_at ? formatExactTime(ts(o.created_at)) : '—' },
    { label: 'Created by', value: o.created_by ?? '—' },
    { label: 'Past revisions kept', value: o.keep_revisions != null ? String(o.keep_revisions) : `${planKeep.value} (plan default)` },
    { label: 'Object id', value: o.object_id, mono: true },
  ]
})

// ── history ────────────────────────────────────────────────────────────────
const feed = useActivityFeed(() => (object.value ? `/objects/${encodeURIComponent(name.value)}/history` : null))
const callFilter = ref<'all' | 'calls' | 'failed'>('all')
const shownRows = computed(() => feed.rows.value.filter(r => callFilter.value === 'all'
  || (callFilter.value === 'calls' ? isCall(r) : failed(r))))
function rowIcon(row: ActivityRow): string {
  if (row.event_type === 'ActiveObjectCreated') return row.meta.cloned_from ? 'mdi-content-copy' : 'mdi-shape-plus-outline'
  if (row.event_type === 'ActiveObjectUpgraded') return 'mdi-arrow-up-bold-circle-outline'
  if (row.meta.method === '__restore__') return 'mdi-restore'
  return failed(row) ? 'mdi-alert-circle-outline' : 'mdi-play-circle-outline'
}
const rowColor = (row: ActivityRow) => (failed(row) ? 'error' : row.event_type === 'ActiveObjectCalled' ? 'primary' : 'info')

/** What saved a revision: its call (method + job) from the history loaded. */
function madeBy(revision: number): { text: string; job?: string } | null {
  const row = feed.rows.value.find(r => r.meta?.revision === revision && (r.event_type !== 'ActiveObjectCalled' || r.meta.outcome === 'completed')
    && (r.event_type !== 'ActiveObjectCalled' || (r.meta.changed?.length ?? 0) > 0 || r.meta.method === '__restore__'))
  if (!row) return null
  const job = row.meta.job_id && !String(row.meta.job_id).startsWith('restore-') ? String(row.meta.job_id) : undefined
  return { text: objectEventTitle(row), job }
}

// ── revisions ──────────────────────────────────────────────────────────────
const viewing = ref(0)
const pastState = ref<ObjectState | null>(null)
const pastError = ref<unknown>(null)
async function view(revision: number) {
  viewing.value = revision
  pastError.value = null
  if (state.value && revision === state.value.revision) { pastState.value = state.value; return }
  pastState.value = null
  try {
    pastState.value = await api<ObjectState>(`/objects/${encodeURIComponent(name.value)}/state?revision=${revision}`)
  } catch (err) { pastError.value = err }
}
watch([tab, state], () => { if (tab.value === 'revisions' && state.value && !pastState.value) view(viewing.value || state.value.revision) })
const restoring = ref(false)
async function restore() {
  restoring.value = true
  try {
    const out = await api<{ revision: number }>(`/objects/${encodeURIComponent(name.value)}/restore`, { method: 'POST', body: { revision: viewing.value } })
    notifier.success(`Revision ${viewing.value} restored as revision ${out.revision}`)
    await afterChange()
    view(out.revision)
  } catch (err) {
    notifier.error(err, "It couldn't be restored")
  } finally {
    restoring.value = false
  }
}

// ── settings ───────────────────────────────────────────────────────────────
const limits = ref<Record<string, any>>({})
const planKeep = computed(() => limits.value.object_keep_revisions ?? 10)
const maxKeep = computed<number | null>(() => limits.value.object_max_keep_revisions ?? null)
const keepNow = computed(() => object.value?.keep_revisions ?? planKeep.value)
const keepInput = ref<number | null>(null)
const saving = ref(false)
watch(object, o => { if (o && !saving.value) keepInput.value = o.keep_revisions ?? null }, { immediate: true })
const keepError = computed(() => (keepInput.value != null && (keepInput.value < 0 || (maxKeep.value != null && keepInput.value > maxKeep.value))
  ? `From 0 to ${maxKeep.value}` : undefined))
const lowering = computed(() => keepInput.value != null && keepInput.value < keepNow.value && (state.value?.revisions.length ?? 1) - 1 > keepInput.value)
async function saveKeep() {
  saving.value = true
  try {
    await api(`/objects/${encodeURIComponent(name.value)}`, { method: 'PATCH', body: { keep_revisions: keepInput.value === null || (keepInput.value as any) === '' ? null : keepInput.value } })
    notifier.success('Saved')
    await store.load(true)
  } catch (err) {
    notifier.error(err, "It couldn't be saved")
  } finally {
    saving.value = false
  }
}
const limitFacts = computed(() => {
  const most = limits.value.object_max_state_bytes as number | null | undefined
  return [
    { label: 'State size', value: most ? `${formatBytes(object.value?.size_bytes)} of ${formatBytes(most)}` : `${formatBytes(object.value?.size_bytes)} (no limit)` },
    { label: 'Values shown in full up to', value: formatBytes(limits.value.object_hot_max_bytes ?? 4096) },
    { label: 'Past revisions', value: `${Math.max(0, (state.value?.revisions.length ?? 1) - 1)} kept of ${keepNow.value}${maxKeep.value != null ? ` (the plan allows up to ${maxKeep.value})` : ''}` },
  ]
})

// ── actions ────────────────────────────────────────────────────────────────
const callOpen = ref(false)
const callMethod = ref<string | null>(null)
const openCall = (m: string | null) => { callMethod.value = m; callOpen.value = true }
const nameOpen = ref(false)
const nameMode = ref<'clone' | 'upgrade'>('clone')
const openName = (mode: 'clone' | 'upgrade') => { nameMode.value = mode; nameOpen.value = true }
async function remove() {
  if (object.value && await actions.removeObject(object.value)) router.push(`/ves/${id.value}/objects`)
}
async function afterChange() {
  await store.load(true)
  await Promise.all([loadState(), feed.refresh()])
}

// ── loading + live ─────────────────────────────────────────────────────────
const loaded = ref(false)
async function load() {
  await store.load(true)
  loaded.value = true
  await Promise.all([loadState(), feed.load()])
}
onMounted(async () => {
  load()
  try { limits.value = (await api<{ plan_limits: Record<string, any> }>('/ve')).plan_limits ?? {} } catch { /* defaults */ }
})
watch([name, vemUp], () => { state.value = null; pastState.value = null; viewing.value = 0; load() })
watch(() => object.value?.status, (s, before) => { if (s === 'ready' && before === 'creating') loadState() })
usePolling(afterChange, 30_000)
useVeLive(() => (vemUp.value && object.value ? `objects/${object.value.object_id}` : null), () => afterChange())
</script>

<style scoped>
.axo-tabs { border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
</style>
