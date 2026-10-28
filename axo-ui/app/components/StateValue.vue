<template>
  <div class="axo-tree" :class="{ 'axo-mono text-body-2': depth === 0 }">
    <template v-if="isBranch">
      <button type="button" class="axo-tree__toggle" :aria-expanded="open" @click="open = !open">
        <v-icon size="16">{{ open ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
        <span v-if="label != null" class="axo-tree__key">{{ label }}:</span>
        <span class="axo-muted">{{ summary }}</span>
      </button>
      <div v-if="open" class="axo-tree__children">
        <StateValue v-for="[k, v] in shown" :key="k" :label="k" :value="v" :depth="depth + 1" />
        <div v-if="entries.length > shown.length" class="text-caption axo-muted py-1">
          … and {{ entries.length - shown.length }} more
          <a href="#" class="axo-link ml-1" @click.prevent="limit += 100">Show 100 more</a>
        </div>
      </div>
    </template>
    <div v-else class="axo-tree__leaf">
      <span v-if="label != null" class="axo-tree__key">{{ label }}:</span>
      <span :class="leafClass">{{ leafText }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
// A saved JSON value as a tree: lists and dicts open and close (the first
// two levels open), other values on one line. Long lists show 100 at a time.
const props = withDefaults(defineProps<{ value: unknown; label?: string | null; depth?: number }>(), { label: null, depth: 0 })
const isBranch = computed(() => props.value !== null && typeof props.value === 'object')
const entries = computed<Array<[string, unknown]>>(() => (Array.isArray(props.value)
  ? props.value.map((v, i) => [String(i), v] as [string, unknown])
  : isBranch.value ? Object.entries(props.value as Record<string, unknown>) : []))
const open = ref(props.depth < 2 && entries.value.length <= 50)
const limit = ref(100)
const shown = computed(() => entries.value.slice(0, limit.value))
const summary = computed(() => (Array.isArray(props.value)
  ? `[${entries.value.length} item${entries.value.length === 1 ? '' : 's'}]`
  : `{${entries.value.length} key${entries.value.length === 1 ? '' : 's'}}`))
const leafText = computed(() => (props.value === null ? 'None' : typeof props.value === 'string' ? JSON.stringify(props.value)
  : typeof props.value === 'boolean' ? (props.value ? 'True' : 'False') : String(props.value)))
const leafClass = computed(() => (props.value === null || typeof props.value === 'boolean' ? 'text-info'
  : typeof props.value === 'string' ? 'text-success' : 'text-primary'))
</script>

<style scoped>
.axo-tree__toggle { display: inline-flex; align-items: center; gap: 4px; background: none; border: 0; padding: 1px 0; color: inherit; cursor: pointer; font: inherit; }
.axo-tree__children { padding-left: 18px; border-left: 1px dashed rgba(var(--v-border-color), var(--v-border-opacity)); margin-left: 7px; }
.axo-tree__leaf { padding: 1px 0 1px 20px; word-break: break-word; }
.axo-tree__key { margin-right: 6px; }
</style>
