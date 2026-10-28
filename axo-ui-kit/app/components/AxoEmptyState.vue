<template>
  <div class="axo-empty text-center py-10 px-4">
    <v-avatar class="axo-soft mb-4" size="72">
      <v-icon color="primary" size="38">{{ icon }}</v-icon>
    </v-avatar>
    <div class="text-h6 font-weight-medium">{{ title }}</div>
    <div v-if="text" class="text-body-2 axo-muted mt-1 mx-auto" style="max-width: 440px">{{ text }}</div>
    <div v-if="$slots.default" class="mt-5"><slot /></div>
  </div>
</template>

<script setup lang="ts">
// Nothing here yet: an icon, one line on what goes here, and the action.
withDefaults(defineProps<{ icon?: string; title: string; text?: string }>(), { icon: 'mdi-shape-outline' })
</script>
