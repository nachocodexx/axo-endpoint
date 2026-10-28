<template>
  <div style="max-width: 880px">
    <AxoPageHeader title="Account" icon="mdi-account-circle-outline" subtitle="Who you are on axo, your password and your teams." />

    <AxoPanel title="Profile" icon="mdi-card-account-details-outline" stacked class="mb-4"
              help="Your name and email come from sign-up and can't be changed here yet.">
      <div class="d-flex align-center ga-4 mb-5">
        <v-avatar size="56" class="axo-gradient"><span class="text-h6 font-weight-bold text-white">{{ me.initials || '·' }}</span></v-avatar>
        <div class="min-w-0">
          <div class="text-h6 font-weight-bold text-truncate">{{ me.displayName }}</div>
          <div class="text-body-2 axo-muted text-truncate">{{ me.me?.email || me.me?.username }}</div>
        </div>
      </div>
      <AxoKeyValue :items="profile" />
    </AxoPanel>

    <AxoPanel title="Password" icon="mdi-lock-reset" stacked class="mb-4"
              help="After the change you stay signed in here with the new password; other places you signed in keep working until their session ends.">
      <div style="max-width: 460px">
        <AxoPasswordForm />
      </div>
    </AxoPanel>

    <AxoPanel title="Your teams" icon="mdi-account-group-outline" stacked :flush="!!teams.length">
      <div v-if="!teams.length" class="text-body-2 axo-muted">You aren't in any team.</div>
      <v-list v-else density="compact" class="bg-transparent py-0">
        <v-list-item v-for="t in teams" :key="t.team_id" :to="t.to" prepend-icon="mdi-account-group-outline" :title="t.name">
          <template #append><v-chip size="small" variant="tonal">{{ ROLE_TITLES[t.role] ?? t.role }}</v-chip></template>
        </v-list-item>
      </v-list>
    </AxoPanel>
  </div>
</template>

<script setup lang="ts">
// The person's own account: profile (read only for now: the Control Plane
// can't change a name or email yet), a new password, and their teams with
// their role in each.
useHead({ title: 'Account · axo' })
const me = useMeStore()
const router = useRouter()
onMounted(() => { if (!me.me) me.load().catch(() => null) })

const PLATFORM_ROLES: Record<string, string> = { admin: 'Platform Admin', user: 'Member', guest: 'Guest (invited by email)' }
const ROLE_TITLES: Record<string, string> = { owner: 'Owner', admin: 'Admin', member: 'Member' }

const profile = computed(() => {
  const m = me.me
  const name = [m?.first_name, m?.last_name].filter(Boolean).join(' ')
  return [
    { label: 'Username', value: m?.username, mono: true },
    { label: 'Name', value: name || null },
    { label: 'Email', value: m?.email || null },
    { label: 'Role on the platform', value: m ? (PLATFORM_ROLES[m.platform_role] ?? m.platform_role) : null },
  ]
})
// A team links to its page where the app has one (axo-ui's /teams/{name}).
const teams = computed(() => (me.me?.teams ?? []).map((t) => {
  const to = `/teams/${encodeURIComponent(t.name)}`
  return { ...t, to: router.resolve(to).matched.length ? to : undefined }
}))
</script>
