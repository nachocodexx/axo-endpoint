<template>
  <v-app-bar color="#000000" flat density="comfortable">
    <NuxtLink :to="session ? cfg.home : '/login'" class="d-flex align-center ml-4 text-decoration-none">
      <img src="/axo-logo.png" alt="axo" width="30" height="30">
      <span class="ml-3 text-white font-weight-bold text-h6">{{ cfg.appName }}</span>
    </NuxtLink>
    <v-spacer />
    <v-btn icon variant="text" color="white" :aria-label="isDark ? 'Light theme' : 'Dark theme'" @click="toggle">
      <v-icon>{{ isDark ? 'mdi-weather-sunny' : 'mdi-weather-night' }}</v-icon>
    </v-btn>
    <AxoBtn v-if="!session" to="/login" class="mr-4" size="small">Sign in</AxoBtn>
  </v-app-bar>
  <v-main>
    <v-container class="pa-4 pa-md-8" style="max-width: 1400px">
      <slot />
    </v-container>
  </v-main>
  <AxoSnackbars />
  <AxoConfirmDialog />
</template>

<script setup lang="ts">
// Pages anyone can open (the error catalog while signed out).
const cfg = useAppConfig().axo
const session = useAuthSession()
const { isDark, toggle } = useThemeSetting()
</script>
