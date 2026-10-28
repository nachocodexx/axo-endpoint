<template>
  <v-menu v-if="ves.list.length" location="bottom start" max-height="420">
    <template #activator="{ props: menu }">
      <v-btn v-bind="menu" variant="text" color="white" class="text-none ml-1 ml-sm-2 px-2 axo-ve-switcher"
             :aria-label="`VE: ${current?.name ?? 'none picked'}`">
        <v-icon start size="18">mdi-cube-outline</v-icon>
        <span class="text-truncate">{{ current?.name ?? 'VEs' }}</span>
        <v-icon end size="18" class="d-none d-sm-inline">mdi-unfold-more-horizontal</v-icon>
      </v-btn>
    </template>
    <v-list density="compact" min-width="260" rounded="lg">
      <v-list-subheader>Your VEs</v-list-subheader>
      <v-list-item v-for="v in ves.list" :key="v.ve_id" :active="v.ve_id === current?.ve_id" color="primary"
                   :title="v.name" :subtitle="`${planName(v.plan_id)} · ${roleTitle(v.my_role)}`" @click="go(v.ve_id)">
        <template #append><AxoStatusChip :value="v.lifecycle" size="x-small" /></template>
      </v-list-item>
      <v-divider class="my-1" />
      <v-list-item prepend-icon="mdi-view-list-outline" title="All VEs" to="/ves" />
      <v-list-item prepend-icon="mdi-plus" title="Rent a VE" to="/ves/new" />
    </v-list>
  </v-menu>
</template>

<script setup lang="ts">
// The VE picker in the top bar. Switching keeps the section you're in
// (Functions of one VE -> Functions of the other), but not a page deeper
// than that, which only exists in the first VE.
const ves = useVesStore()
const route = useRoute()
const current = computed(() => (route.path.startsWith('/ves/') ? ves.byId(String(route.params.id ?? '')) : undefined))
const planName = (id: string) => (id ? id[0]!.toUpperCase() + id.slice(1) : '—')

function go(id: string) {
  const section = current.value ? route.path.split('/')[3] : undefined
  navigateTo(section ? `/ves/${id}/${section}` : `/ves/${id}`)
}
</script>

<style scoped>
.axo-ve-switcher { max-width: 260px; }
@media (max-width: 599px) { .axo-ve-switcher { max-width: 130px; } }
.axo-ve-switcher :deep(.v-btn__content) { min-width: 0; }
</style>
