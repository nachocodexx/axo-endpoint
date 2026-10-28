<template>
  <v-app>
    <NuxtLoadingIndicator color="linear-gradient(90deg, #00C2FF, #0086FF, #5B5BFF)" :height="3" />
    <NuxtLayout>
      <NuxtPage />
    </NuxtLayout>
  </v-app>
</template>

<script setup lang="ts">
// The saved light / dark theme before the first paint (no flash on reload).
useThemeSetting()
</script>
