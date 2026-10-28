<template>
  <div>
    <div v-if="!names.length" class="pa-4 text-body-2 axo-muted">No attributes: nothing is saved.</div>
    <template v-for="(n, i) in names" :key="n">
      <v-divider v-if="i" />
      <div class="axo-attr px-4 py-3" :class="{ 'axo-attr--changed': diff(n) }">
        <div class="d-flex align-center flex-wrap ga-2 mb-1">
          <span class="axo-mono font-weight-medium">{{ n }}</span>
          <span class="text-caption axo-muted">{{ valueKind(attributes[n]!) }} · {{ formatBytes(attributes[n]!.size) }}</span>
          <v-chip v-if="diff(n)" size="x-small" variant="tonal" color="warning">{{ diff(n) }}</v-chip>
        </div>
        <StateValue v-if="'value' in attributes[n]!" :value="attributes[n]!.value" />
        <div v-else class="text-caption axo-muted">
          {{ attributes[n]!.kind === 'json' ? 'A big value: ' : '' }}kept in the VE's objects bucket and loaded only when a method reads it.
        </div>
      </div>
    </template>
    <template v-if="removed.length">
      <v-divider />
      <div class="px-4 py-3 text-caption">
        <v-chip size="x-small" variant="tonal" color="warning" class="mr-2">not in this revision</v-chip>
        <span class="axo-mono">{{ removed.join(', ') }}</span> (it has them now)
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { StateAttribute } from '~/utils/objects'

// An object's saved attributes (sorted): kind, size and, for small JSON
// values, the value as a tree. With `compare` (the values now), each one
// that differs is marked — viewing a past revision.
const props = defineProps<{ attributes: Record<string, StateAttribute>; compare?: Record<string, StateAttribute> }>()
const names = computed(() => Object.keys(props.attributes).sort())
const removed = computed(() => (props.compare ? Object.keys(props.compare).filter(n => !(n in props.attributes)).sort() : []))
function diff(n: string): string | null {
  if (!props.compare) return null
  const now = props.compare[n]
  if (!now) return 'not there now'
  return now.hash !== props.attributes[n]!.hash ? 'different from now' : null
}
</script>

<style scoped>
.axo-attr--changed { background: rgba(var(--v-theme-warning), 0.06); }
</style>
