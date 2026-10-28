<template>
  <div style="max-width: 1100px">
    <AxoPageHeader title="Help & FAQ" icon="mdi-help-circle-outline"
                   subtitle="Short guides with one example each, the references, and the questions people ask most." />

    <div class="text-overline axo-muted mb-2">Guides</div>
    <v-row density="compact" class="mb-6">
      <v-col v-for="g in GUIDES" :key="g.id" cols="12" sm="6" lg="4">
        <v-card :to="`/help/${g.id}`" variant="outlined" class="axo-help-card pa-4 h-100">
          <div class="d-flex align-center ga-3 mb-2">
            <v-avatar class="axo-soft" rounded="lg" size="36"><v-icon color="primary" size="20">{{ g.icon }}</v-icon></v-avatar>
            <span class="text-subtitle-1 font-weight-bold">{{ g.title }}</span>
          </div>
          <div class="text-body-2 axo-muted">{{ g.summary }}</div>
        </v-card>
      </v-col>
    </v-row>

    <div class="text-overline axo-muted mb-2">References</div>
    <v-row density="compact" class="mb-6">
      <v-col cols="12" sm="6">
        <v-card to="/errors" variant="outlined" class="axo-help-card pa-4 h-100">
          <div class="d-flex align-center ga-3 mb-2">
            <v-avatar class="axo-soft" rounded="lg" size="36"><v-icon color="primary" size="20">mdi-book-alert-outline</v-icon></v-avatar>
            <span class="text-subtitle-1 font-weight-bold">Error catalog</span>
          </div>
          <div class="text-body-2 axo-muted">Every error the platform can show: its name and number, what it means and what to do.</div>
        </v-card>
      </v-col>
      <v-col cols="12" sm="6">
        <v-card :href="apiDocs" target="_blank" rel="noopener" variant="outlined" class="axo-help-card pa-4 h-100">
          <div class="d-flex align-center ga-3 mb-2">
            <v-avatar class="axo-soft" rounded="lg" size="36"><v-icon color="primary" size="20">mdi-api</v-icon></v-avatar>
            <span class="text-subtitle-1 font-weight-bold">API reference</span>
            <v-icon size="16" class="axo-muted">mdi-open-in-new</v-icon>
          </div>
          <div class="text-body-2 axo-muted">Every call of the platform's HTTP API. Authorize (the padlock) with an API key to try them.</div>
        </v-card>
      </v-col>
    </v-row>

    <div class="text-overline axo-muted mb-2">Frequent questions</div>
    <v-expansion-panels variant="accordion" class="mb-4">
      <v-expansion-panel v-for="f in FAQ" :key="f.q" :title="f.q">
        <v-expansion-panel-text>
          <p v-for="(p, i) in f.a" :key="i" class="text-body-2" :class="{ 'mt-2': i }">{{ p }}</p>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </div>
</template>

<script setup lang="ts">
import { FAQ, GUIDES } from '../../utils/help'

// Help & FAQ (account menu): the guides (utils/help.ts), the error catalog
// and the Control Plane's API reference (/docs), and the frequent questions.
useHead({ title: 'Help & FAQ · axo' })
const apiDocs = `${String(useRuntimeConfig().public.apiBase).replace(/\/$/, '')}/docs`
</script>

<style scoped>
.axo-help-card { transition: border-color 0.15s; }
.axo-help-card:hover { border-color: rgb(var(--v-theme-primary)); }
</style>
