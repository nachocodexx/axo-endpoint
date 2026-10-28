<template>
  <div class="axo-add" :class="{ 'is-folded': folded }">
    <div class="axo-add__head">
      <span v-if="!folded" class="text-subtitle-2">Add a step</span>
      <v-spacer />
      <v-btn :icon="folded ? 'mdi-chevron-right' : 'mdi-chevron-left'" size="small" variant="text"
             :aria-label="folded ? 'Show the steps panel' : 'Hide the steps panel'" @click="folded = !folded" />
    </div>
    <template v-if="!folded">
      <v-text-field v-model="search" density="compact" variant="outlined" placeholder="Search" prepend-inner-icon="mdi-magnify"
                    hide-details clearable class="mx-3 mb-2" />
      <div class="axo-add__hint">Drag onto the canvas, or click to place it after the selected step.</div>
      <div class="axo-add__list">
        <div v-for="g in groups" :key="g.title">
          <div class="axo-add__group">
            <v-icon :icon="g.icon" size="15" class="mr-1" />{{ g.title }} <span class="axo-muted ml-1">{{ g.items.length }}</span>
          </div>
          <div v-for="it in g.items" :key="it.key" class="axo-add__item" draggable="true" role="button" tabindex="0"
               :title="it.hint" @dragstart="onDrag($event, it.payload)" @click="emit('add', it.payload)"
               @keydown.enter.prevent="emit('add', it.payload)">
            <v-icon :icon="it.icon" size="16" :color="it.color" />
            <div class="min-w-0">
              <div class="text-truncate text-body-2">{{ it.title }}</div>
              <div v-if="it.sub" class="text-truncate axo-muted text-caption">{{ it.sub }}</div>
            </div>
          </div>
          <div v-if="!g.items.length" class="axo-muted text-caption px-4 pb-2">{{ search ? 'Nothing matches' : g.empty }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
// The designer's left panel: functions, objects (each method), other
// choreographies (not this one, nor one that would call it back) and buckets
// of this VE to add as steps, and "Start when…" (a trigger). Each item is
// dragged onto the canvas or clicked. Folded by default on tablets.
import type { ChoreographyRow } from '~/utils/choreographies'
import type { FunctionRow } from '~/utils/functions'
import type { ActiveObject } from '~/utils/objects'

export interface AddPayload {
  kind: 'function' | 'object' | 'choreography' | 'bucket' | 'trigger'
  choreography_id?: string
  choreography_name?: string
  function_id?: string
  function_name?: string
  object_name?: string
  method?: string
  bucket_name?: string
  event_type?: string
}
const props = defineProps<{ functions: FunctionRow[]; objects: ActiveObject[]; methodsOf: (o: ActiveObject) => string[]; buckets: string[]
  choreographies: ChoreographyRow[]; /** This one and those that would call it back. */ blocked: Set<string> }>()
const emit = defineEmits<{ (e: 'add', p: AddPayload): void }>()
const folded = defineModel<boolean>('folded', { default: false })
const search = ref('')

interface Item { key: string; title: string; sub?: string; icon: string; color?: string; hint?: string; payload: AddPayload }
const has = (...texts: Array<string | undefined>) => {
  const q = (search.value || '').trim().toLowerCase()
  return !q || texts.some(t => t?.toLowerCase().includes(q))
}
const groups = computed(() => [
  { title: 'Functions', icon: 'mdi-function-variant', empty: 'No functions in this VE yet',
    items: props.functions.filter(f => !f.deleted && has(f.name)).map<Item>(f => ({
      key: `f:${f.function_id}`, title: f.name, sub: `v${f.defaultVersion}`, icon: 'mdi-function-variant', color: 'primary',
      payload: { kind: 'function', function_id: f.function_id, function_name: f.name } })) },
  { title: 'Objects', icon: 'mdi-shape-outline', empty: 'No objects in this VE yet',
    items: props.objects.flatMap(o => props.methodsOf(o).filter(m => has(o.name, m, o.class_name)).map<Item>(m => ({
      key: `o:${o.name}:${m}`, title: `${o.name}.${m}`, sub: o.class_name, icon: 'mdi-shape-outline', color: 'info',
      payload: { kind: 'object', object_name: o.name, method: m } }))) },
  { title: 'Choreographies', icon: 'mdi-layers-triple-outline', empty: 'No other choreography to call',
    items: props.choreographies.filter(c => !props.blocked.has(c.choreography_id) && has(c.name, c.description ?? '')).map<Item>(c => ({
      key: `c:${c.choreography_id}`, title: c.name, sub: `v${c.version} · ${c.steps} step${c.steps === 1 ? '' : 's'}`,
      icon: 'mdi-layers-triple-outline', color: 'success',
      hint: 'Runs the whole choreography as one step: its own run, linked to this one',
      payload: { kind: 'choreography', choreography_id: c.choreography_id, choreography_name: c.name } })) },
  { title: 'Buckets', icon: 'mdi-bucket-outline', empty: 'No bucket you can read',
    items: props.buckets.filter(b => has(b)).map<Item>(b => ({
      key: `b:${b}`, title: b, icon: 'mdi-bucket-outline', color: 'brand-3',
      hint: 'At the start it reads files; after a step it saves the results',
      payload: { kind: 'bucket', bucket_name: b } })) },
  { title: 'Start when…', icon: 'mdi-lightning-bolt', empty: '',
    items: ([
      { key: 't:file', title: 'A new file arrives', sub: 'in a bucket', icon: 'mdi-file-upload-outline', color: 'warning',
        payload: { kind: 'trigger', event_type: 'file.written' } },
      { key: 't:event', title: 'An event is sent', sub: 'e.g. order.paid', icon: 'mdi-lightning-bolt', color: 'warning',
        payload: { kind: 'trigger', event_type: '' } },
    ] as Item[]).filter(i => has(i.title, i.sub)) },
])

function onDrag(ev: DragEvent, p: AddPayload) {
  ev.dataTransfer?.setData('application/x-axo-step', JSON.stringify(p))
  if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'copy'
}
</script>

<style scoped>
.axo-add { width: 260px; flex: 0 0 auto; display: flex; flex-direction: column; border-right: 1px solid rgba(var(--v-border-color), 0.12);
  background: rgb(var(--v-theme-surface)); transition: width 0.25s ease; min-height: 0; }
.axo-add.is-folded { width: 44px; }
.axo-add__head { display: flex; align-items: center; padding: 6px 4px 6px 14px; min-height: 44px; }
.axo-add__hint { font-size: 0.72rem; color: rgb(var(--v-theme-muted)); padding: 0 14px 8px; }
.axo-add__list { overflow-y: auto; flex: 1 1 auto; padding-bottom: 12px; }
.axo-add__group { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; padding: 10px 14px 4px;
  color: rgb(var(--v-theme-muted)); display: flex; align-items: center; }
.axo-add__item { display: flex; align-items: center; gap: 10px; margin: 2px 8px; padding: 6px 8px; border-radius: 8px; cursor: grab;
  border: 1px solid transparent; transition: background 0.15s ease, border-color 0.15s ease; }
.axo-add__item:hover, .axo-add__item:focus-visible { background: rgba(var(--v-theme-primary), 0.06); border-color: rgba(var(--v-theme-primary), 0.2); outline: none; }
.axo-add__item:active { cursor: grabbing; }
</style>
