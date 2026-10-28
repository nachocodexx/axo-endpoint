<template>
  <div style="max-width: 880px">
    <AxoEmptyState v-if="!guide" icon="mdi-help-circle-outline" title="No guide with this name" text="It may have moved.">
      <v-btn variant="tonal" class="text-none mt-3" to="/help">All guides</v-btn>
    </AxoEmptyState>
    <template v-else>
      <AxoPageHeader :title="guide.title" :icon="guide.icon" :subtitle="guide.summary"
                     :crumbs="[{ title: 'Help & FAQ', to: '/help' }, { title: guide.title, disabled: true }]" />

      <section v-for="s in guide.sections" :key="s.title" class="mb-8">
        <h2 class="text-h6 font-weight-bold mb-2">{{ s.title }}</h2>
        <p v-for="(p, i) in s.text" :key="i" class="text-body-1 mb-2">{{ p }}</p>
        <div v-if="s.code" class="axo-help-code mt-3">
          <AxoCode :model-value="s.code" language="python" readonly min-height="40px" />
          <v-btn size="small" variant="text" icon="mdi-content-copy" class="axo-help-copy" aria-label="Copy the code" @click="copy(s.code)" />
        </div>
      </section>

      <div v-if="hasCode" class="text-caption axo-muted mb-6">{{ MADE_UP_NOTE }}</div>

      <template v-if="next.length">
        <v-divider class="mb-4" />
        <div class="text-overline axo-muted mb-2">Next</div>
        <div class="d-flex flex-wrap ga-2">
          <v-btn v-for="n in next" :key="n.id" :to="`/help/${n.id}`" variant="tonal" color="primary" class="text-none" :prepend-icon="n.icon">
            {{ n.title }}
          </v-btn>
          <v-btn to="/help" variant="text" class="text-none">All guides</v-btn>
        </div>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { GUIDES, MADE_UP_NOTE } from '../../utils/help'

// One guide (utils/help.ts): what it is, one example (copyable), where it
// is in the app, and the guides to read next.
const route = useRoute()
const guide = computed(() => GUIDES.find(g => g.id === route.params.guide))
const next = computed(() => (guide.value?.next ?? []).map(id => GUIDES.find(g => g.id === id)).filter(g => !!g))
const hasCode = computed(() => !!guide.value?.sections.some(s => s.code))
useHead({ title: computed(() => `${guide.value?.title ?? 'Guide'} · Help · axo`) })

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); useNotifier().success('Copied') } catch { useNotifier().warning("Couldn't copy") }
}
</script>

<style scoped>
.axo-help-code { position: relative; }
.axo-help-copy { position: absolute; top: 4px; right: 4px; z-index: 2; }
</style>
