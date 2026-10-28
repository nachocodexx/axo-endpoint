<template>
  <dl class="axo-kv" :style="{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }">
    <div v-for="item in items" :key="item.label" class="axo-kv__item">
      <dt class="text-caption axo-muted d-flex align-center ga-1">
        {{ item.label }}
        <AxoHelp v-if="item.help" :title="item.label" :text="item.help" />
      </dt>
      <dd class="text-body-2 font-weight-medium" :class="{ 'axo-mono axo-selectable': item.mono }">
        <slot :name="item.slot ?? item.label" :item="item">
          <NuxtLink v-if="item.to" :to="item.to" class="axo-link">{{ item.value ?? '—' }}</NuxtLink>
          <template v-else>{{ item.value ?? '—' }}</template>
        </slot>
      </dd>
    </div>
  </dl>
</template>

<script setup lang="ts">
// Label / value pairs in a grid (a VE's or endpoint's details panel).
export interface KeyValueItem { label: string; value?: string | number | null; help?: string; mono?: boolean; slot?: string; to?: string }
withDefaults(defineProps<{ items: KeyValueItem[]; columns?: number }>(), { columns: 3 })
</script>

<style scoped>
.axo-kv { display: grid; gap: 16px 24px; margin: 0; }
.axo-kv dd { margin: 2px 0 0; word-break: break-word; }
@media (max-width: 700px) { .axo-kv { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; } }
</style>
