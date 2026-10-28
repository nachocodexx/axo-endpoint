<template>
  <AxoShell :nav="nav" :home="cfg.home" :badge="cfg.badge || undefined">
    <template #bar><VeSwitcher /></template>
    <template #bar-end><BucketUploadButton /></template>
    <template #user-menu>
      <v-list-item prepend-icon="mdi-inbox-arrow-down-outline" title="Inbox" to="/inbox">
        <template v-if="inbox.waiting.value" #append>
          <v-badge :content="inbox.waiting.value" color="error" inline />
        </template>
      </v-list-item>
      <v-list-item prepend-icon="mdi-key-outline" title="API keys" to="/account/api-keys" />
    </template>
    <slot />
    <ClientOnly><BucketUploadPanel /></ClientOnly>
  </AxoShell>
</template>

<script setup lang="ts">
// Signed-in pages of axo-ui: the kit's shell, plus the VE switcher in the
// top bar, the uploads (top-bar button + panel, see useBucketUploads) and,
// inside a VE (/ves/{id}/...), that VE's own section in the
// navigation. Keeps the VEs store current from the live channel.
const cfg = useAppConfig().axo
const route = useRoute()
const ves = useVesStore()
const session = useAuthSession()

// The VE's pages; "Overview" covers its tabs (monitoring, settings, ...).
const VE_SECTIONS = [
  { path: '', icon: 'mdi-cube-outline', title: 'Overview' },
  { path: 'endpoints', icon: 'mdi-server-outline', title: 'Endpoints' },
  { path: 'functions', icon: 'mdi-function-variant', title: 'Functions' },
  { path: 'objects', icon: 'mdi-shape-outline', title: 'Objects' },
  { path: 'choreographies', icon: 'mdi-sitemap-outline', title: 'Choreographies' },
  { path: 'events', icon: 'mdi-lightning-bolt-outline', title: 'Events' },
  { path: 'jobs', icon: 'mdi-briefcase-outline', title: 'Jobs' },
]
const SECTION_PATHS = VE_SECTIONS.map(s => s.path).filter(Boolean)

const veId = computed(() => (route.path.startsWith('/ves/') && route.params.id ? String(route.params.id) : null))

const nav = computed(() => {
  const inVe = veId.value !== null
  const items = cfg.nav.map((item: any) => (item.to === '/ves' && inVe ? { ...item, active: false } : item))
  if (inVe) {
    const base = `/ves/${veId.value}`
    const section = route.path.slice(base.length).split('/')[1] ?? ''
    items.push({ header: ves.byId(veId.value)?.name ?? 'This VE' })
    for (const s of VE_SECTIONS) {
      const active = s.path ? section === s.path : !SECTION_PATHS.includes(section)
      items.push({ to: s.path ? `${base}/${s.path}` : base, icon: s.icon, title: s.title, active })
    }
  }
  return items
})

// Invitations / transfers waiting for an answer: the account menu's count.
const inbox = useInboxFollow()

const live = useLive()
live.onVe((card, id) => (card ? ves.put(card) : ves.remove(id)))
onMounted(() => { if (session.value && !ves.loaded) ves.load() })
</script>
