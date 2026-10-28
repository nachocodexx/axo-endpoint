<template>
  <NuxtLink :to="`/errors#${name}`" class="axo-error-name" @click.stop>
    <v-chip size="x-small" :color="color" variant="outlined" class="axo-mono">
      {{ name }}<template v-if="code"> · {{ code }}</template>
      <v-icon end size="12">mdi-open-in-new</v-icon>
    </v-chip>
    <v-tooltip activator="parent">What's this error? Open it in the error catalog</v-tooltip>
  </NuxtLink>
</template>

<script setup lang="ts">
// An error's catalog name + number; clicking opens /errors#NAME.
withDefaults(defineProps<{ name: string; code?: number; color?: string }>(), { color: 'error' })
</script>

<style scoped>
.axo-error-name { text-decoration: none; }
</style>
