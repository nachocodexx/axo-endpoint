<template>
  <v-menu location="bottom" :close-on-content-click="false" max-width="360">
    <template #activator="{ props: menu }">
      <v-btn v-bind="menu" icon size="x-small" variant="text" density="comfortable"
             :aria-label="`What is ${title}?`" class="axo-help">
        <v-icon size="18" color="muted">mdi-help-circle-outline</v-icon>
      </v-btn>
    </template>
    <v-card class="pa-4" border>
      <div class="d-flex align-center mb-2">
        <v-icon color="primary" size="20" class="mr-2">mdi-lightbulb-on-outline</v-icon>
        <span class="text-subtitle-1 font-weight-medium">{{ title }}</span>
      </div>
      <div class="text-body-2" style="line-height: 1.55"><slot>{{ text }}</slot></div>
      <div v-if="to" class="mt-3">
        <NuxtLink :to="to" class="text-primary text-body-2">Learn more →</NuxtLink>
      </div>
    </v-card>
  </v-menu>
</template>

<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'

// The "?" next to a panel or page that needs a word of explanation.
// Plain words only: what it shows and what to do with it.
defineProps<{ title: string; text?: string; to?: RouteLocationRaw }>()
</script>
