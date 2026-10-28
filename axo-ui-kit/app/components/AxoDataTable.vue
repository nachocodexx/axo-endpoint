<template>
  <v-card class="axo-table-card">
    <div v-if="searchable || $slots.filters || $slots.actions || title" class="d-flex align-center flex-wrap ga-3 pa-4 pb-2">
      <div v-if="title" class="d-flex align-center ga-1 mr-2">
        <span class="text-subtitle-1 font-weight-medium">{{ title }}</span>
        <v-chip v-if="items.length" size="x-small" variant="tonal" class="ml-1">{{ items.length }}</v-chip>
        <AxoHelp v-if="help" :title="title" :text="help" />
      </div>
      <v-text-field v-if="searchable" v-model="search" prepend-inner-icon="mdi-magnify" :placeholder="searchPlaceholder"
                    hide-details density="compact" clearable style="max-width: 320px; min-width: 200px" />
      <slot name="filters" />
      <v-spacer />
      <slot name="actions" />
    </div>
    <v-data-table
      class="axo-table"
      :headers="headers"
      :items="items"
      :item-value="itemValue"
      :search="search"
      :loading="loading && items.length > 0"
      :items-per-page="itemsPerPage"
      :row-props="rowProps"
      :sort-by="sortBy"
      :show-select="!!selectable"
      :item-selectable="selectable && typeof selectable === 'function' ? selectable : undefined"
      :model-value="selected"
      :hide-default-footer="itemsPerPage === -1"
      hover
      :density="settings.density"
      @click:row="onRow"
      @update:model-value="emit('update:selected', $event)"
    >
      <template v-for="(_, name) in passSlots" #[name]="scope"><slot :name="name" v-bind="scope" /></template>
      <template #loading><AxoSkeleton :lines="4" class="pa-4" /></template>
      <template #no-data>
        <AxoSkeleton v-if="loading" :lines="4" class="pa-4" />
        <slot v-else name="empty">
          <AxoEmptyState :icon="emptyIcon" :title="emptyTitle" :text="emptyText" />
        </slot>
      </template>
    </v-data-table>
  </v-card>
</template>

<script setup lang="ts" generic="T extends Record<string, any>">
import type { RouteLocationRaw } from 'vue-router'

// A console-style list (like a cloud console): title + count, search,
// filters and actions on top, clickable rows, shimmer while loading, an
// empty state, and rows waiting for the server dimmed (optimistic UI:
// `pending(row)`); optional checkboxes (`selectable` + v-model:selected);
// `items-per-page="-1"` shows every row without the footer. Every v-data-table `item.<key>` slot passes through.
const { settings } = useSettings()
const props = withDefaults(defineProps<{
  headers: any[]
  items: T[]
  itemValue?: string
  loading?: boolean
  title?: string
  help?: string
  searchable?: boolean
  searchPlaceholder?: string
  itemsPerPage?: number
  sortBy?: Array<{ key: string; order?: 'asc' | 'desc' }>
  rowTo?: (row: T) => RouteLocationRaw | undefined
  /** Rows that do something on click (the `row` event) without going anywhere. */
  clickable?: (row: T) => boolean
  pending?: (row: T) => boolean
  /** Checkboxes per row (true, or which rows may be ticked); the ticked
   *  rows' item values come back through v-model:selected. */
  selectable?: boolean | ((row: T) => boolean)
  selected?: unknown[]
  emptyIcon?: string
  emptyTitle?: string
  emptyText?: string
}>(), {
  itemValue: 'id', loading: false, searchable: true, searchPlaceholder: 'Search', itemsPerPage: 25,
  emptyTitle: 'Nothing here yet',
})
const emit = defineEmits<{ (e: 'row', row: T): void; (e: 'update:selected', v: unknown[]): void }>()
const slots = useSlots()
const search = ref('')

const passSlots = computed(() => Object.fromEntries(
  Object.entries(slots).filter(([k]) => k.startsWith('item.') || k.startsWith('header.') || k === 'expanded-row'),
))

function rowProps({ item }: { item: T }) {
  const classes = []
  if (props.rowTo || props.clickable?.(item)) classes.push('axo-clickable')
  if (props.pending?.(item)) classes.push('axo-pending')
  return { class: classes.join(' ') }
}

function onRow(_: Event, { item }: { item: T }) {
  emit('row', item)
  const to = props.rowTo?.(item)
  if (to) navigateTo(to)
}
</script>
