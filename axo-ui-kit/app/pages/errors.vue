<template>
  <NuxtLayout :name="session ? 'default' : 'public'">
    <AxoPageHeader title="Error catalog" icon="mdi-book-alert-outline"
                   :crumbs="session ? [{ title: 'Help & FAQ', to: '/help' }, { title: 'Error catalog', disabled: true }] : undefined"
                   subtitle="Every error the platform can show you: its number, what it means, and what to do."
                   help="Each error message in the app shows a name and a number (like NOT_FOUND · 9502). Click it to land here on that error. The hint says what usually fixes it." />

    <div class="d-flex flex-wrap ga-3 mb-4">
      <v-text-field v-model="search" prepend-inner-icon="mdi-magnify" placeholder="Search by name, number or words"
                    hide-details clearable style="max-width: 420px; min-width: 240px" />
      <v-chip-group v-model="part" selected-class="text-primary" column>
        <v-chip value="" variant="outlined" filter>All</v-chip>
        <v-chip v-for="(title, key) in catalog?.components" :key="key" :value="key" variant="outlined" filter>{{ title }}</v-chip>
      </v-chip-group>
    </div>

    <AxoLoader v-if="loading" block text="Loading the catalog" />
    <AxoErrorAlert v-else-if="failed" :error="failed" title="The catalog couldn't be loaded" :retry="reload" />
    <AxoEmptyState v-else-if="!shown.length" icon="mdi-text-search" title="No error matches" text="Try another word or number." />

    <template v-else>
      <section v-for="group in groups" :key="group.key" class="mb-8">
        <div class="text-overline axo-muted mb-2">{{ group.title }} · {{ group.items.length }}</div>
        <v-row density="compact">
          <v-col v-for="e in group.items" :id="e.name" :key="e.code" cols="12" md="6" xl="4">
            <v-card :class="['pa-4 h-100 axo-error-card', { 'axo-error-card--target': target === e.name }]">
              <div class="d-flex align-center flex-wrap ga-2 mb-2">
                <v-chip size="small" color="error" variant="tonal" class="axo-mono font-weight-bold axo-selectable">{{ e.name }}</v-chip>
                <span class="axo-mono axo-muted">#{{ e.code }}</span>
                <v-spacer />
                <v-chip v-if="e.http_status" size="x-small" variant="outlined">HTTP {{ e.http_status }}</v-chip>
                <v-btn icon size="x-small" variant="text" :aria-label="`Link to ${e.name}`" @click="copyLink(e.name)">
                  <v-icon size="16">mdi-link-variant</v-icon>
                </v-btn>
              </div>
              <div class="text-body-1 mb-2 axo-selectable">{{ e.description }}</div>
              <div class="d-flex text-body-2 axo-hint pa-3">
                <v-icon size="18" color="primary" class="mr-2 mt-0">mdi-lightbulb-on-outline</v-icon>
                <span class="axo-selectable">{{ e.hint }}</span>
              </div>
            </v-card>
          </v-col>
        </v-row>
      </section>
    </template>
  </NuxtLayout>
</template>

<script setup lang="ts">
// /errors (no sign-in needed): the shared error catalog, grouped by part of
// the platform, searchable; /errors#NAME scrolls to and highlights one.
definePageMeta({ auth: false, layout: false })
const session = useAuthSession()
useHead({ title: 'Error catalog · axo' })

const { catalog, load } = useErrorCatalog()
const route = useRoute()
const search = ref('')
const part = ref<string>('')
const loading = ref(!catalog.value)
const failed = ref<unknown>(null)
const target = computed(() => decodeURIComponent(route.hash.replace(/^#/, '')))

async function reload() {
  loading.value = true
  failed.value = null
  try {
    const c = await load()
    if (!c) throw new Error('The platform could not be reached.')
  } catch (err) { failed.value = err } finally { loading.value = false }
}

const shown = computed(() => {
  const q = (search.value || '').trim().toLowerCase()
  return (catalog.value?.errors ?? []).filter(e => (!part.value || e.component === part.value) && (!q
    || e.name.toLowerCase().includes(q) || String(e.code).includes(q)
    || e.description.toLowerCase().includes(q) || e.hint.toLowerCase().includes(q)))
})

const groups = computed(() => Object.entries(catalog.value?.components ?? {})
  .map(([key, title]) => ({ key, title, items: shown.value.filter(e => e.component === key) }))
  .filter(g => g.items.length))

async function scrollToTarget() {
  if (!target.value) return
  await nextTick()
  document.getElementById(target.value)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

onMounted(async () => { await reload(); scrollToTarget() })
watch(target, scrollToTarget)

async function copyLink(name: string) {
  try {
    await navigator.clipboard.writeText(`${location.origin}/errors#${name}`)
    useNotifier().success(`Link to ${name} copied`)
  } catch { useNotifier().warning("Couldn't copy the link") }
}
</script>

<style scoped>
.axo-error-card { scroll-margin-top: 96px; transition: box-shadow 0.3s ease, border-color 0.3s ease; }
.axo-error-card--target { border-color: rgb(var(--v-theme-primary)) !important; box-shadow: 0 0 0 3px rgba(0, 134, 255, 0.25); }
.axo-hint { background: rgba(0, 134, 255, 0.06); border-radius: 8px; }
</style>
