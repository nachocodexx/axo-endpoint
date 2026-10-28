<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <div class="text-h6 d-flex align-center ga-1">Events
          <AxoHelp title="Events" text="An event is a small message someone (or the platform) sends: a type like order.paid and some data. Choreographies with a trigger on that type start a run with the data as their input. A new file in a bucket is the event file.written." />
        </div>
        <div class="text-body-2 axo-muted">What starts choreographies on their own.</div>
      </div>
      <v-spacer />
      <v-btn v-if="can('developer') && tab === 'types'" variant="tonal" class="text-none" prepend-icon="mdi-plus" :disabled="!vemUp" @click="openType(null)">New event type</v-btn>
      <AxoBtn v-if="can('developer')" prepend-icon="mdi-send-outline" :disabled="!vemUp" @click="openSend(null)">Send an event</AxoBtn>
    </div>
    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">The VE is {{ ve?.lifecycle?.toLowerCase() }}: its events show here once it runs.</v-alert>
    <template v-else>
      <v-tabs v-model="tab" color="primary" class="mb-4 axo-tabs">
        <v-tab value="types" prepend-icon="mdi-shape-outline">Types</v-tab>
        <v-tab value="recent" prepend-icon="mdi-history">Recent</v-tab>
      </v-tabs>
      <AxoErrorAlert v-if="error" :error="error" title="The events couldn't load" :retry="load" class="mb-4" />

      <AxoDataTable v-if="tab === 'types'" :headers="typeHeaders" :items="types" item-value="type" :loading="loading && !types.length"
                    :clickable="() => true" empty-icon="mdi-lightning-bolt-outline" empty-title="No event types yet"
                    empty-text="Send an event, or describe a type first." @row="(t: EventType) => openType(t)">
        <template #item.type="{ item }">
          <div class="py-2">
            <span class="axo-mono text-body-2 font-weight-medium">{{ item.type }}</span>
            <v-chip v-if="item.built_in" size="x-small" variant="tonal" class="ml-2">platform</v-chip>
            <div v-if="item.description" class="text-caption axo-muted text-truncate" style="max-width: 360px">{{ item.description }}</div>
          </div>
        </template>
        <template #item.fields="{ item }">
          <span class="text-body-2">{{ item.fields.map((f: { name: string }) => f.name).join(', ') || '—' }}</span>
        </template>
        <template #item.used_by="{ item }">
          <div class="d-flex flex-wrap ga-1">
            <v-chip v-for="u in item.used_by ?? []" :key="u.choreography_id" size="x-small" variant="outlined" :to="`/ves/${id}/choreographies/${encodeURIComponent(u.choreography)}`" @click.stop>{{ u.choreography }}</v-chip>
            <span v-if="!item.used_by?.length" class="axo-muted">—</span>
          </div>
        </template>
        <template #item.count="{ item }">
          <span class="text-body-2">{{ item.count != null ? formatNumber(item.count) : '—' }}</span>
          <div v-if="item.last_seen" class="text-caption axo-muted">last {{ timeAgo(ts(item.last_seen)) }}</div>
        </template>
        <template #item.actions="{ item }">
          <div class="d-flex justify-end ga-1" @click.stop>
            <v-btn size="small" variant="text" class="text-none" prepend-icon="mdi-code-tags" @click="howTo = item.type">How to send</v-btn>
            <v-btn v-if="can('developer') && !item.built_in" size="small" variant="text" icon="mdi-send-outline" :aria-label="`Send ${item.type}`" @click="openSend(item)" />
          </div>
        </template>
      </AxoDataTable>

      <template v-else>
        <div class="d-flex align-center ga-2 mb-3">
          <v-select v-model="typeFilter" :items="types.map(t => t.type)" density="compact" variant="outlined" hide-details clearable placeholder="Any type" class="axo-filter" />
        </div>
        <AxoDataTable :headers="eventHeaders" :items="events" item-value="id" :searchable="false" :items-per-page="-1" :loading="loading && !events.length"
                      :clickable="() => true" empty-icon="mdi-history" empty-title="No events yet" @row="(e: ChEvent) => (shownEvent = e)">
          <template #item.type="{ item }"><span class="axo-mono text-body-2">{{ item.type }}</span></template>
          <template #item.time="{ item }"><span class="text-body-2 text-no-wrap" :title="formatExactTime(ts(item.time))">{{ timeAgo(ts(item.time)) }}</span></template>
          <template #item.from="{ item }"><span class="text-body-2">{{ item.sent_by_name || item.source || '—' }}</span></template>
          <template #item.data="{ item }"><span class="axo-mono text-caption text-truncate d-inline-block" style="max-width: 320px">{{ JSON.stringify(item.data) }}</span></template>
          <template #item.runs="{ item }">
            <div class="d-flex flex-wrap ga-1" @click.stop>
              <v-chip v-for="r in item.runs ?? []" :key="r.run_id" size="x-small" variant="tonal" color="primary"
                      :to="`/ves/${id}/choreographies/${encodeURIComponent(r.choreography)}/runs/${r.run_id}`">{{ r.choreography }}</v-chip>
              <span v-if="!item.runs?.length" class="axo-muted text-body-2">none</span>
            </div>
          </template>
        </AxoDataTable>
        <div v-if="next" class="d-flex justify-center mt-3"><v-btn variant="text" prepend-icon="mdi-chevron-down" :loading="loading" @click="loadEvents(true)">Load more</v-btn></div>
      </template>
    </template>

    <!-- one event -->
    <v-dialog :model-value="!!shownEvent" max-width="640" @update:model-value="!$event && (shownEvent = null)">
      <v-card v-if="shownEvent" :title="shownEvent.type" :subtitle="`${shownEvent.id} · ${formatExactTime(ts(shownEvent.time))}`">
        <v-card-text>
          <AxoKeyValue :columns="2" :items="[{ label: 'Source', value: shownEvent.source ?? '—', mono: true }, { label: 'Subject', value: shownEvent.subject ?? '—' },
                                             { label: 'Sent by', value: shownEvent.sent_by_name ?? shownEvent.sent_by ?? '—' }, { label: 'Runs started', value: shownEvent.runs?.length ?? 0 }]" class="mb-3" />
          <StateValue :value="shownEvent.data" label="data" />
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn variant="text" @click="shownEvent = null">Close</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <!-- how to send -->
    <v-dialog :model-value="!!howTo" max-width="680" @update:model-value="!$event && (howTo = null)">
      <v-card v-if="howTo" :title="`Sending ${howTo}`">
        <v-card-text>
          <AxoCode :model-value="emitSnippet(id, howTo === 'file.written' ? 'order.paid' : howTo)" language="python" readonly min-height="60px" />
          <div v-if="howTo === 'file.written'" class="text-body-2 mt-3">file.written is sent by the platform when a file is written in a bucket: upload a file to start the runs.</div>
          <div v-else class="text-body-2 mt-3">The same <span class="axo-mono">id</span> twice is one event (safe to retry). Also <span class="axo-mono">POST /ves/{{ id }}/api/events</span> with <span class="axo-mono">{"type", "data", "event_id"}</span>.</div>
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn variant="text" @click="howTo = null">Close</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <!-- a type -->
    <v-dialog v-model="typeOpen" max-width="680" scrollable>
      <v-card :title="typeForm.existing ? typeForm.type : 'New event type'">
        <v-card-text>
          <v-text-field v-if="!typeForm.existing" v-model="typeForm.type" density="compact" variant="outlined" label="Type" placeholder="e.g. order.paid"
                        persistent-placeholder :error-messages="typeNameError" hide-details="auto" class="mb-3" />
          <v-textarea v-model="typeForm.description" density="compact" variant="outlined" label="What it means" rows="2" auto-grow hide-details class="mb-3"
                      :disabled="typeForm.builtIn || !can('developer')" />
          <div class="text-subtitle-2 mb-1">Fields of its data</div>
          <div v-for="(f, i) in typeForm.fields" :key="i" class="d-flex flex-wrap ga-2 mb-2">
            <v-text-field v-model="f.name" density="compact" variant="outlined" label="Name" hide-details style="flex: 1 1 140px" :disabled="typeForm.builtIn || !can('developer')" />
            <v-select v-model="f.type" :items="['text', 'number', 'yes/no', 'list', 'object', 'any']" density="compact" variant="outlined" label="Kind" hide-details
                      style="flex: 0 1 130px" :disabled="typeForm.builtIn || !can('developer')" />
            <v-text-field v-model="f.description" density="compact" variant="outlined" label="Meaning" hide-details style="flex: 2 1 200px" :disabled="typeForm.builtIn || !can('developer')" />
            <v-btn v-if="!typeForm.builtIn && can('developer')" icon="mdi-close" size="small" variant="text" aria-label="Remove field" @click="typeForm.fields.splice(i, 1)" />
          </div>
          <v-btn v-if="!typeForm.builtIn && can('developer')" size="small" variant="text" prepend-icon="mdi-plus" @click="typeForm.fields.push({ name: '', type: 'text', description: '' })">Field</v-btn>
          <div v-if="typeForm.usedBy.length" class="text-body-2 mt-3">Used by {{ typeForm.usedBy.join(', ') }}.</div>
        </v-card-text>
        <v-card-actions>
          <v-btn v-if="typeForm.existing && !typeForm.builtIn && can('admin')" color="error" variant="text" :disabled="typeForm.usedBy.length > 0"
                 :title="typeForm.usedBy.length ? 'A trigger uses it' : undefined" @click="deleteType">Delete</v-btn>
          <v-spacer />
          <v-btn variant="text" @click="typeOpen = false">{{ typeForm.builtIn || !can('developer') ? 'Close' : 'Cancel' }}</v-btn>
          <v-btn v-if="!typeForm.builtIn && can('developer')" color="primary" variant="flat" :loading="busy" :disabled="!!typeNameError" @click="saveType">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- send -->
    <v-dialog v-model="sendOpen" max-width="620">
      <v-card title="Send an event">
        <v-card-text>
          <v-combobox v-model="sendForm.type" :items="types.filter(t => !t.built_in).map(t => t.type)" density="compact" variant="outlined" label="Type"
                      placeholder="e.g. order.paid" persistent-placeholder :error-messages="sendTypeError" hide-details="auto" class="mb-3" />
          <v-textarea v-model="sendForm.data" variant="outlined" label="Data (JSON)" rows="5" auto-grow class="axo-mono" :error-messages="sendDataError" hide-details="auto" />
          <v-text-field v-model="sendForm.id" density="compact" variant="outlined" label="Event id" placeholder="made for you" persistent-placeholder class="mt-3"
                        hint="The same id twice is one event." persistent-hint />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="sendOpen = false">Cancel</v-btn>
          <AxoBtn variant="flat" :loading="busy" :disabled="!!sendTypeError || !!sendDataError" @click="send">Send</AxoBtn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
// The VE's events: Types (built-in and people's own, their fields, which
// choreographies' triggers use them, how to send one; describe or delete a
// type) and Recent (the event log, with the runs each one started).
import type { ChEvent, EventType } from '~/utils/choreographies'

const { id, ve, can, vemUp, api } = useCurrentVe()
useHead(() => ({ title: `Events · ${ve.value?.name ?? 'VE'} · axo` }))
const notifier = useNotifier()
const { confirm } = useConfirm()
const route = useRoute()
const router = useRouter()
const tab = ref<string>(route.query.tab === 'recent' ? 'recent' : 'types')
watch(tab, t => router.replace({ query: { ...route.query, tab: t === 'types' ? undefined : t } }))

const types = ref<EventType[]>([])
const events = ref<ChEvent[]>([])
const next = ref<{ before: string; before_id: string } | null>(null)
const typeFilter = ref<string | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
async function loadTypes() { types.value = await api<EventType[]>('/event-types') }
async function loadEvents(more = false) {
  const page = await api<{ events: ChEvent[]; next: { before: string; before_id: string } | null }>('/events',
    { query: { type: typeFilter.value ?? undefined, ...(more && next.value ? next.value : {}) } })
  events.value = more ? [...events.value, ...page.events] : page.events
  next.value = page.next
}
async function load() {
  if (!vemUp.value) return
  loading.value = true
  try { await Promise.all([loadTypes(), loadEvents()]); error.value = null } catch (err) { error.value = err } finally { loading.value = false }
}
onMounted(load)
watch([vemUp, typeFilter], load)
usePolling(load, 30_000)

const WIDE = { headerProps: { class: 'd-none d-md-table-cell' }, cellProps: { class: 'd-none d-md-table-cell' } }
const typeHeaders = [
  { title: 'Type', key: 'type' }, { title: 'Fields', key: 'fields', sortable: false, ...WIDE },
  { title: 'Used by', key: 'used_by', sortable: false }, { title: 'Sent', key: 'count', ...WIDE },
  { title: '', key: 'actions', sortable: false, align: 'end' as const },
]
const eventHeaders = [
  { title: 'Type', key: 'type', sortable: false }, { title: 'When', key: 'time', sortable: false },
  { title: 'From', key: 'from', sortable: false, ...WIDE }, { title: 'Data', key: 'data', sortable: false, ...WIDE },
  { title: 'Runs started', key: 'runs', sortable: false },
]
const shownEvent = ref<ChEvent | null>(null)
const howTo = ref<string | null>(null)

const NAME_RE = /^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$/
const nameProblem = (t: string) => (!t ? 'Name the type' : !NAME_RE.test(t) ? 'Lowercase words joined by dots, like order.paid'
  : /^(file|schedule|run|axo)\./.test(t) ? "Names starting with file. schedule. run. axo. are the platform's" : undefined)

// a type
const typeOpen = ref(false)
const typeForm = reactive({ existing: false, builtIn: false, type: '', description: '', fields: [] as Array<{ name: string; type: string; description?: string }>, usedBy: [] as string[] })
function openType(t: EventType | null) {
  Object.assign(typeForm, { existing: !!t, builtIn: !!t?.built_in, type: t?.type ?? '', description: t?.description ?? '',
    fields: (t?.fields ?? []).map(f => ({ ...f })), usedBy: (t?.used_by ?? []).map(u => u.choreography) })
  typeOpen.value = true
}
const typeNameError = computed(() => (typeForm.existing ? undefined : nameProblem(typeForm.type) ?? (types.value.some(t => t.type === typeForm.type) ? 'This type already exists' : undefined)))
const busy = ref(false)
async function saveType() {
  busy.value = true
  try {
    await api(`/event-types/${encodeURIComponent(typeForm.type)}`, { method: 'PUT', body: { description: typeForm.description, fields: typeForm.fields.filter(f => f.name) } })
    typeOpen.value = false
    notifier.success(`${typeForm.type} saved`)
    await loadTypes()
  } catch (err) { notifier.error(err, "The type couldn't be saved") } finally { busy.value = false }
}
async function deleteType() {
  if (!(await confirm({ title: `Delete ${typeForm.type}?`, text: 'Only its description goes: events already sent stay in the log.', danger: true, confirmText: 'Delete' }))) return
  try { await api(`/event-types/${encodeURIComponent(typeForm.type)}`, { method: 'DELETE' }); typeOpen.value = false; await loadTypes() }
  catch (err) { notifier.error(err, "The type couldn't be deleted") }
}

// send
const sendOpen = ref(false)
const sendForm = reactive({ type: '', data: '{}', id: '' })
function openSend(t: EventType | null) {
  const sample = Object.fromEntries((t?.fields ?? []).map(f => [f.name, f.type === 'number' ? 0 : f.type === 'yes/no' ? false : '']))
  Object.assign(sendForm, { type: t?.type ?? '', data: JSON.stringify(sample, null, 2), id: '' })
  sendOpen.value = true
}
const sendTypeError = computed(() => nameProblem(sendForm.type ?? ''))
const sendDataError = computed(() => { try { JSON.parse(sendForm.data || 'null'); return undefined } catch (e) { return `Not valid JSON: ${(e as Error).message}` } })
async function send() {
  busy.value = true
  try {
    const answer = await api<ChEvent>('/events', { method: 'POST', body: { type: sendForm.type, data: JSON.parse(sendForm.data || 'null'), event_id: sendForm.id || undefined } })
    sendOpen.value = false
    const n = answer.runs?.length ?? 0
    notifier.success(answer.duplicate ? 'Already sent before: nothing new started' : n ? `Sent: ${n} run${n === 1 ? '' : 's'} started` : 'Sent: no trigger matched')
    tab.value = 'recent'
    await load()
  } catch (err) { notifier.error(err, "The event couldn't be sent") } finally { busy.value = false }
}
</script>
