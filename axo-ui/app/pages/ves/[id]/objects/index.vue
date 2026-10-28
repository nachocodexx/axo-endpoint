<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <div class="text-h6 d-flex align-center ga-1">Objects
          <AxoHelp title="Active objects" text="An object is a named instance of an Axo class that lives in the VE: what its attributes hold is saved after every call, so the next call starts from there. Calls to one object run one at a time. A class is the code (like a function's code): every change is a new version." />
        </div>
        <div class="text-body-2 axo-muted">
          {{ store.objects.value.length }} object{{ store.objects.value.length === 1 ? '' : 's' }} ·
          {{ store.classes.value.length }} class{{ store.classes.value.length === 1 ? '' : 'es' }}
        </div>
      </div>
      <v-spacer />
      <template v-if="can('developer')">
        <v-btn v-if="tab === 'classes'" variant="tonal" class="text-none" prepend-icon="mdi-plus" :disabled="!vemUp"
               :to="`/ves/${id}/objects/classes/new`">New class</v-btn>
        <AxoBtn prepend-icon="mdi-shape-plus-outline" :disabled="!vemUp || !store.classes.value.length" @click="newOpen = true">New object</AxoBtn>
      </template>
    </div>

    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: its objects show here once it runs (a sleeping VE wakes with its next call).
    </v-alert>
    <AxoErrorAlert v-if="store.error.value" :error="store.error.value" title="The objects couldn't load" :retry="() => store.load(true)" class="mb-4" />

    <template v-if="vemUp">
      <v-tabs v-model="tab" color="primary" class="mb-4 axo-tabs">
        <v-tab value="objects" prepend-icon="mdi-shape-outline">Objects</v-tab>
        <v-tab value="classes" prepend-icon="mdi-code-braces">Classes</v-tab>
      </v-tabs>

      <AxoEmptyState v-if="loaded && !store.classes.value.length && !store.objects.value.length" icon="mdi-shape-outline"
                     title="No objects yet" :text="can('developer') ? 'Register a class with New class, or write one in Python.' : 'Developers make them, here or in Python.'">

        <NuxtLink to="/help/objects" class="text-label-large axo-link d-inline-block mt-1">See the guide</NuxtLink>
        <br/>
        <v-btn v-if="can('developer')" variant="tonal" class="text-none mt-3" prepend-icon="mdi-plus" :to="`/ves/${id}/objects/classes/new`">New class</v-btn>
      </AxoEmptyState>

      <template v-else-if="tab === 'objects'">
        <div class="d-flex align-center flex-wrap ga-2 mb-3">
          <v-text-field v-model="search" density="compact" variant="outlined" hide-details clearable prepend-inner-icon="mdi-magnify"
                        placeholder="Name" class="axo-filter" />
          <v-select v-model="classFilter" :items="classChoices" density="compact" variant="outlined" hide-details clearable
                    placeholder="Any class" class="axo-filter" />
        </div>
        <AxoDataTable :headers="objectHeaders" :items="shownObjects" item-value="name" :searchable="false"
                      :loading="store.loading.value && !store.objects.value.length"
                      :row-to="(o) => `/ves/${id}/objects/${encodeURIComponent(o.name)}`" :sort-by="[{ key: 'name', order: 'asc' }]"
                      empty-icon="mdi-shape-outline" :empty-title="store.objects.value.length ? 'No object matches' : 'No objects yet'"
                      :empty-text="store.objects.value.length ? 'Change the filters.' : can('developer') ? 'Make one with New object.' : 'Developers make them.'">
          <template #item.name="{ item }">
            <div class="d-flex align-center ga-2 py-2">
              <span class="text-body-2 font-weight-medium axo-mono">{{ item.name }}</span>
              <AxoStatusChip v-if="item.status !== 'ready'" kind="object" :value="item.status" size="x-small" />
            </div>
          </template>
          <template #item.class="{ item }">
            <span class="text-body-2 text-no-wrap">{{ item.class_name }} v{{ item.class_version }}</span>
            <v-chip v-if="newestOf(item) > item.class_version" size="x-small" variant="tonal" color="primary" class="ml-2">v{{ newestOf(item) }} available</v-chip>
          </template>
          <template #item.size_bytes="{ item }"><span class="text-body-2 text-no-wrap">{{ formatBytes(item.size_bytes) }}</span></template>
          <template #item.revision="{ item }"><span class="text-body-2">{{ item.revision }}</span></template>
          <template #item.calls="{ item }"><span class="text-body-2">{{ formatNumber(item.calls) }}</span></template>
          <template #item.last="{ item }">
            <span v-if="item.last_call_at" class="text-body-2 text-no-wrap" :title="formatExactTime(ts(item.last_call_at))">
              <span :class="item.last_outcome && item.last_outcome !== 'completed' ? 'text-error' : ''">{{ objectMethodTitle(item.last_method) }}</span>
              <span class="axo-muted"> · {{ timeAgo(ts(item.last_call_at)) }}</span>
            </span>
            <span v-else class="axo-muted">—</span>
          </template>
          <template #item.created="{ item }">
            <span class="text-body-2 text-no-wrap" :title="formatExactTime(ts(item.created_at))">{{ item.created_at ? timeAgo(ts(item.created_at)) : '—' }}</span>
          </template>
          <template #item.actions="{ item }">
            <div v-if="can('developer')" class="d-flex justify-end align-center ga-1" @click.stop>
              <v-btn size="small" variant="tonal" color="primary" class="text-none" prepend-icon="mdi-play"
                     :disabled="item.status !== 'ready'" @click="openCall(item.name)">Call…</v-btn>
              <v-menu location="bottom end">
                <template #activator="{ props: menu }">
                  <v-btn v-bind="menu" icon="mdi-dots-vertical" size="small" variant="text" :aria-label="`More for ${item.name}`" />
                </template>
                <v-list density="compact">
                  <v-list-item prepend-icon="mdi-content-copy" title="Clone…" @click="openName(item.name, 'clone')" />
                  <v-list-item prepend-icon="mdi-arrow-up-bold-circle-outline" title="Upgrade…" :disabled="newestOf(item) <= item.class_version"
                               @click="openName(item.name, 'upgrade')" />
                  <v-list-item v-if="can('admin')" prepend-icon="mdi-delete-outline" title="Delete…" base-color="error" @click="actions.removeObject(item)" />
                </v-list>
              </v-menu>
            </div>
          </template>
        </AxoDataTable>
      </template>

      <AxoDataTable v-else :headers="classHeaders" :items="store.classes.value" item-value="class_name" :searchable="true"
                    search-placeholder="Class" :row-to="(c) => `/ves/${id}/objects/classes/${encodeURIComponent(c.class_name)}`"
                    :sort-by="[{ key: 'class_name', order: 'asc' }]" empty-icon="mdi-code-braces" empty-title="No classes yet"
                    :empty-text="can('developer') ? 'Register one with New class or the SDK.' : 'Developers register them.'">
        <template #item.class_name="{ item }">
          <div class="py-2 min-w-0">
            <div class="text-body-2 font-weight-medium axo-mono">{{ item.class_name }}</div>
            <div v-if="item.latest.description" class="text-caption axo-muted text-truncate" style="max-width: 320px">{{ item.latest.description }}</div>
          </div>
        </template>
        <template #item.newest="{ item }"><span class="text-body-2">v{{ item.latest.version }}</span></template>
        <template #item.versions="{ item }"><span class="text-body-2">{{ item.versions.length }}</span></template>
        <template #item.methods="{ item }">
          <span class="text-body-2 axo-mono text-truncate d-inline-block" style="max-width: 260px">{{ methodNames(item.latest) }}</span>
        </template>
        <template #item.objects="{ item }">
          <span class="text-body-2">{{ store.objectsOn(item.class_name).length }}</span>
          <span v-if="olderCount(item)" class="text-caption text-warning"> · {{ olderCount(item) }} on older versions</span>
        </template>
        <template #item.registered="{ item }">
          <span class="text-body-2 text-no-wrap" :title="formatExactTime(ts(item.latest.created_at))">{{ item.latest.created_at ? timeAgo(ts(item.latest.created_at)) : '—' }}</span>
        </template>
      </AxoDataTable>
    </template>

    <ObjectNewDialog v-model="newOpen" />
    <ObjectCallDialog v-if="callTarget" v-model="callOpen" :object-name="callTarget" @called="store.load(true)" />
    <ObjectNameDialog v-if="nameTarget" v-model="nameOpen" :object-name="nameTarget" :mode="nameMode" />
  </div>
</template>

<script setup lang="ts">
import type { ActiveObject, ClassRow } from '~/utils/objects'

// The VE's objects (name, class + version with "vN available", size,
// revision, calls, last call, created; Call… and ⋮ clone / upgrade / delete)
// and, on the Classes tab, its classes (newest version, methods, objects on
// it). Live: read again on every message of the VEM's "objects" feed.
const { id, ve, can, vemUp } = useCurrentVe()
const store = useObjects()
const actions = useObjectActions()
useHead(() => ({ title: `Objects · ${ve.value?.name ?? 'VE'} · axo` }))

const route = useRoute()
const router = useRouter()
const tab = ref<string>(route.query.tab === 'classes' ? 'classes' : 'objects')
const search = ref((route.query.q as string) ?? '')
const classFilter = ref<string | null>((route.query.class as string) ?? null)
watch([tab, search, classFilter], () => router.replace({ query: { ...route.query, tab: tab.value === 'classes' ? 'classes' : undefined,
  q: search.value || undefined, class: classFilter.value || undefined } }))

const classChoices = computed(() => store.classes.value.map(c => c.class_name))
const shownObjects = computed(() => store.objects.value.filter(o =>
  (!search.value || o.name.toLowerCase().includes(search.value.toLowerCase())) && (!classFilter.value || o.class_name === classFilter.value)))
const newestOf = (o: ActiveObject) => store.classOf(o.class_name)?.latest.version ?? o.class_version
const olderCount = (c: ClassRow) => store.objectsOn(c.class_name).filter(o => o.class_version < c.latest.version).length

const WIDE = { headerProps: { class: 'd-none d-md-table-cell' }, cellProps: { class: 'd-none d-md-table-cell' } }
const objectHeaders = [
  { title: 'Name', key: 'name', sortable: true },
  { title: 'Class', key: 'class', sortable: false },
  { title: 'Size', key: 'size_bytes', sortable: true, ...WIDE },
  { title: 'Revision', key: 'revision', sortable: true, ...WIDE },
  { title: 'Calls', key: 'calls', sortable: true, ...WIDE },
  { title: 'Last call', key: 'last', sortable: false },
  { title: 'Created', key: 'created', sortable: false, headerProps: { class: 'd-none d-lg-table-cell' }, cellProps: { class: 'd-none d-lg-table-cell' } },
  { title: '', key: 'actions', sortable: false, align: 'end' as const },
]
const classHeaders = [
  { title: 'Class', key: 'class_name', sortable: true },
  { title: 'Newest', key: 'newest', sortable: false },
  { title: 'Versions', key: 'versions', sortable: false, ...WIDE },
  { title: 'Methods', key: 'methods', sortable: false, ...WIDE },
  { title: 'Objects', key: 'objects', sortable: false },
  { title: 'Last registered', key: 'registered', sortable: false, ...WIDE },
]

const newOpen = ref(false)
const callOpen = ref(false)
const callTarget = ref<string | null>(null)
const openCall = (name: string) => { callTarget.value = name; callOpen.value = true }
const nameOpen = ref(false)
const nameTarget = ref<string | null>(null)
const nameMode = ref<'clone' | 'upgrade'>('clone')
const openName = (name: string, mode: 'clone' | 'upgrade') => { nameTarget.value = name; nameMode.value = mode; nameOpen.value = true }

const loaded = ref(false)
async function load() { await store.load(true); loaded.value = true }
watch([id, vemUp], load)
onMounted(load)
usePolling(() => store.load(true), 30_000)
useVeLive(() => (vemUp.value ? 'objects' : null), () => store.load(true))
</script>

<style scoped>
.axo-filter { flex: 0 1 220px; min-width: 160px; }
.axo-tabs { border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
@media (max-width: 600px) { .axo-filter { flex-basis: 100%; } }
</style>
