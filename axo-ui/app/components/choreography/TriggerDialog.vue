<template>
  <v-dialog :model-value="!!trigger" max-width="720" scrollable @update:model-value="!$event && emit('close')">
    <v-card v-if="trigger && t">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon :icon="isFile ? 'mdi-file-upload-outline' : 'mdi-lightning-bolt'" color="warning" />
        Start when…
        <v-spacer />
        <AxoHelp title="Triggers" text="A trigger starts a run on its own: when a new file arrives in a bucket, or when someone sends an event (client.emit or POST /events). The event's data fills the first steps' parameters by name; the whole event is ctx.event / self.event." />
      </v-card-title>
      <v-card-text>
        <v-radio-group :model-value="isFile ? 'file' : 'event'" inline hide-details :disabled="readonly" class="mb-3"
                       @update:model-value="t.event_type = $event === 'file' ? 'file.written' : (customTypes[0]?.type ?? '')">
          <v-radio value="file" label="A new file arrives" />
          <v-radio value="event" label="An event is sent" />
        </v-radio-group>

        <template v-if="isFile">
          <div class="d-flex flex-wrap ga-3">
            <v-autocomplete v-model="t.bucket" :items="buckets" density="compact" variant="outlined" label="In bucket" :disabled="readonly"
                            hide-details="auto" style="flex: 2 1 240px" :error-messages="t.bucket ? undefined : 'Pick a bucket'" />
            <v-text-field v-model="t.prefix" density="compact" variant="outlined" label="Only names starting with" placeholder="e.g. raw/"
                          persistent-placeholder :disabled="readonly" hide-details style="flex: 1 1 180px" />
          </div>
          <div v-if="t.bucket && !readsBucket" class="text-caption text-warning mt-2">
            No step reads {{ t.bucket }} yet: add a bucket step for it at the start.
          </div>
        </template>
        <template v-else>
          <v-combobox v-model="t.event_type" :items="typeItems" item-title="title" item-value="value" :return-object="false" density="compact"
                      variant="outlined" label="Event type" placeholder="e.g. order.paid" persistent-placeholder :disabled="readonly"
                      :error-messages="typeProblem" hide-details="auto">
            <template #item="{ props: p, item }"><v-list-item v-bind="p" :subtitle="item.subtitle" /></template>
          </v-combobox>
          <div v-if="typeInfo?.description" class="text-caption axo-muted mt-1">{{ typeInfo.description }}</div>
        </template>

        <div class="d-flex align-center mt-4 mb-1">
          <span class="text-subtitle-2">Only when</span>
          <v-spacer />
          <v-switch :model-value="!!t.condition" color="primary" density="compact" hide-details inset :disabled="readonly"
                    :label="t.condition ? 'rules on the data' : 'always'" @update:model-value="t.condition = $event ? { match: 'all', rules: [{ field: fields[0] ?? '', op: '==', value: '' }] } : null" />
        </div>
        <v-expand-transition>
          <div v-if="t.condition">
            <ChoreographyConditionBuilder v-model="t.condition" :fields="fields" :operators="info?.condition_operators ?? []" field-label="Field of the data" />
            <div class="text-body-2 axo-muted">{{ conditionText(t.condition) }}</div>
          </div>
        </v-expand-transition>
        <div v-if="used.length && !readonly" class="mt-3">
          <div class="text-caption axo-muted mb-1">Used before</div>
          <div class="d-flex flex-wrap ga-2">
            <v-chip v-for="u in used" :key="JSON.stringify(u.condition)" size="small" variant="outlined" prepend-icon="mdi-history"
                    @click="t.condition = JSON.parse(JSON.stringify(u.condition))">{{ conditionText(u.condition) }} · {{ u.count }}×</v-chip>
          </div>
        </div>

        <v-select v-model="t.targets" :items="targetItems" multiple chips closable-chips density="compact" variant="outlined"
                  label="Drawn to" :disabled="readonly" class="mt-4" hide-details="auto"
                  hint="Empty = every first step. Every first step runs; this is where the arrow is drawn." persistent-hint />
      </v-card-text>
      <v-card-actions>
        <v-btn v-if="!readonly" color="error" variant="text" prepend-icon="mdi-delete-outline" @click="emit('remove')">Remove trigger</v-btn>
        <v-spacer />
        <v-btn variant="text" @click="emit('close')">{{ readonly ? 'Close' : 'Cancel' }}</v-btn>
        <v-btn v-if="!readonly" color="primary" variant="flat" :disabled="!!typeProblem || (isFile && !t.bucket)" @click="done">Done</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { ChGraph, ChTrigger, DesignerInfo, EventType, Suggestions } from '~/utils/choreographies'

const props = defineProps<{ trigger: ChTrigger | null; graph: ChGraph; info: DesignerInfo | null; eventTypes: EventType[]
  suggestions: Suggestions | null; buckets: string[]; readonly?: boolean; title: (id: string) => string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'save', v: ChTrigger): void; (e: 'remove'): void }>()

const t = ref<ChTrigger | null>(null)
watch(() => props.trigger, (x) => { t.value = x ? { targets: [], ...JSON.parse(JSON.stringify(x)) } : null }, { immediate: true })
const isFile = computed(() => t.value?.event_type === 'file.written')

const customTypes = computed(() => props.eventTypes.filter(e => !e.built_in && e.type !== 'file.written'))
const typeItems = computed(() => {
  const seen = new Map<string, { value: string; title: string; subtitle?: string }>()
  for (const e of customTypes.value) seen.set(e.type, { value: e.type, title: e.type, subtitle: e.description ?? (e.count ? `${e.count} sent` : undefined) })
  for (const s of props.suggestions?.event_types ?? []) if (s.type !== 'file.written' && !seen.has(s.type)) seen.set(s.type, { value: s.type, title: s.type, subtitle: `used in ${s.used_in.join(', ')}` })
  return [...seen.values()]
})
const typeInfo = computed(() => props.eventTypes.find(e => e.type === t.value?.event_type))
const NAME_RE = /^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)*$/
const typeProblem = computed(() => {
  const ty = t.value?.event_type ?? ''
  if (isFile.value) return undefined
  if (!ty) return 'Name the event type'
  if (!NAME_RE.test(ty)) return 'Lowercase words joined by dots, like order.paid'
  if (/^(file|schedule|run|axo)\./.test(ty)) return 'Names starting with file. schedule. run. axo. are the platform\'s'
  return undefined
})
const fields = computed(() => (typeInfo.value?.fields ?? []).map(f => f.name))
const used = computed(() => (props.suggestions?.conditions ?? []).filter(c => c.where === 'trigger' && c.event_type === t.value?.event_type).slice(0, 6))
const readsBucket = computed(() => firstSteps(props.graph).some(n => n.kind === 'bucket' && n.bucket_name === t.value?.bucket))
const targetItems = computed(() => firstSteps(props.graph).map(n => ({ value: n.node_id, title: props.title(n.node_id) })))

function done() {
  if (!t.value) return
  const out: ChTrigger = { ...t.value }
  if (!isFile.value) { delete out.bucket; delete out.prefix }
  if (!out.prefix) delete out.prefix
  if (!out.condition?.rules?.length) out.condition = null
  emit('save', out)
}
</script>
