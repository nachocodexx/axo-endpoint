<template>
  <div class="axo-page-header mb-6">
    <v-breadcrumbs v-if="crumbs?.length" :items="crumbs" class="pa-0 mb-1 text-body-2" density="compact">
      <template #divider><v-icon size="14">mdi-chevron-right</v-icon></template>
    </v-breadcrumbs>
    <div class="d-flex align-center flex-wrap ga-3">
      <v-avatar v-if="icon" class="axo-soft" rounded="lg" size="44">
        <v-icon color="primary" size="26">{{ icon }}</v-icon>
      </v-avatar>
      <div class="flex-grow-1" style="min-width: 0">
        <div class="d-flex align-center ga-1">
          <h1 class="text-h5 font-weight-bold text-truncate">{{ title }}</h1>
          <slot name="title-append" />
          <AxoHelp v-if="help" :title="title" :text="help" />
        </div>
        <div v-if="subtitle || $slots.subtitle" class="text-body-2 axo-muted mt-1">
          <slot name="subtitle">{{ subtitle }}</slot>
        </div>
      </div>
      <div class="axo-gap"><slot name="actions" /></div>
    </div>
  </div>
</template>

<script setup lang="ts">
// The top of every page: breadcrumbs, icon, title, "?" help, actions.
defineProps<{
  title: string
  subtitle?: string
  icon?: string
  help?: string
  crumbs?: Array<{ title: string; to?: string; disabled?: boolean }>
}>()
</script>
