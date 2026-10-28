<template>
  <AxoShell :nav="cfg.nav" :home="cfg.home" :badge="cfg.badge || undefined">
    <slot />
  </AxoShell>
</template>

<script setup lang="ts">
// Signed-in pages: the shell with the app's navigation (app.config.ts → axo.nav).
const cfg = useAppConfig().axo
</script>
