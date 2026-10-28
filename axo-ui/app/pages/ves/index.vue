<template>
  <div>
    <AxoPageHeader title="Virtual Environments" icon="mdi-cube-outline"
                   subtitle="Where your functions, objects and choreographies run."
                   help="A Virtual Environment (VE) is your own set of endpoints with its own data, settings and bill. You rent it by the month; you can also be given a role in someone else's VE.">
      <template #actions>
        <AxoBtn prepend-icon="mdi-plus" to="/ves/new">Rent a VE</AxoBtn>
      </template>
    </AxoPageHeader>

    <AxoErrorAlert v-if="ves.error" :error="ves.error" title="Your VEs couldn't load" :retry="ves.load" class="mb-4" />

    <v-chip-group v-model="filter" mandatory selected-class="text-primary" class="mb-2">
      <v-chip v-for="f in FILTERS" :key="f.value" :value="f.value" variant="outlined" size="small">
        {{ f.title }}<span class="ml-1 axo-muted">· {{ counts[f.value] }}</span>
      </v-chip>
    </v-chip-group>

    <AxoDataTable :headers="headers" :items="shown" item-value="ve_id" :loading="!ves.loaded" :title="FILTERS.find(f => f.value === filter)!.title"
                  :row-to="(v) => `/ves/${v.ve_id}`">
      <template #empty>
        <AxoEmptyState icon="mdi-cube-outline" title="No VEs yet"
                       text="Rent one to start running functions, or ask someone to give you a role in theirs.">
          <AxoBtn prepend-icon="mdi-plus" to="/ves/new" class="mt-2">Rent a VE</AxoBtn>
        </AxoEmptyState>
      </template>
      <template #item.name="{ item }">
        <div class="py-2">
          <div class="font-weight-medium">{{ item.name }}</div>
        </div>
      </template>
      <template #item.owner="{ item }">
        <NuxtLink v-if="item.owner.kind === 'team' && myTeams.has(item.owner.name ?? '')" :to="`/teams/${item.owner.name}`"
                  class="axo-link" @click.stop><v-icon size="16" class="mr-1">mdi-account-group-outline</v-icon>{{ item.owner.name }}</NuxtLink>
        <span v-else-if="item.owner.kind === 'team'"><v-icon size="16" class="mr-1">mdi-account-group-outline</v-icon>{{ item.owner.name }}</span>
        <span v-else>{{ relation(item) === 'mine' ? 'You' : item.owner.name }}</span>
      </template>
      <template #item.plan_id="{ item }">{{ planName(item.plan_id) }}</template>
      <template #item.lifecycle="{ item }">
        <div class="d-flex flex-wrap ga-1">
          <AxoStatusChip :value="item.lifecycle" :reason="item.lifecycle_reason" />
          <AxoStatusChip v-if="item.health" kind="health" :value="item.health" />
        </div>
      </template>
      <template #item.endpoints="{ item }">
        {{ item.endpoints_running }} / {{ item.target_endpoints }}
        <span class="text-caption axo-muted">· {{ item.endpoint_size.name }}</span>
      </template>
      <template #item.my_role="{ item }">{{ roleTitle(item.my_role) }}</template>
      <template #item.period_end="{ item }">
        <span v-if="item.rental.cancel_at_period_end" class="text-warning">Ends {{ formatDate(item.rental.period_end) }}</span>
        <span v-else-if="item.rental.period_end">Renews {{ formatDate(item.rental.period_end) }}</span>
        <span v-else class="axo-muted">—</span>
      </template>
    </AxoDataTable>
  </div>
</template>

<script setup lang="ts">
// Every VE the person can see, live (the store follows the socket).
useHead({ title: 'VEs · axo' })
const ves = useVesStore()
const me = useMeStore()
const route = useRoute()
onMounted(() => ves.load())

// Mine = I own it; Team = one of my teams owns it; Shared = someone gave me a role.
const FILTERS = [
  { value: 'all', title: 'All VEs' }, { value: 'mine', title: 'Mine' },
  { value: 'team', title: 'Team' }, { value: 'shared', title: 'Shared with me' },
] as const
type Filter = typeof FILTERS[number]['value']
const filter = ref<Filter>((FILTERS.find(f => f.value === route.query.show)?.value ?? 'all') as Filter)
watch(filter, f => navigateTo({ query: f === 'all' ? {} : { show: f } }, { replace: true }))
const myTeams = computed(() => new Set((me.me?.teams ?? []).map(t => t.name)))
const myTeamIds = computed(() => new Set((me.me?.teams ?? []).map(t => t.team_id)))
function relation(v: any): Exclude<Filter, 'all'> {
  if (v.owner.kind === 'user' && v.owner.id === me.me?.user_id) return 'mine'
  if (v.owner.kind === 'team' && myTeamIds.value.has(v.owner.id)) return 'team'
  return 'shared'
}
const shown = computed(() => (filter.value === 'all' ? ves.list : ves.list.filter(v => relation(v) === filter.value)))
const counts = computed<Record<Filter, number>>(() => ({
  all: ves.list.length, mine: ves.list.filter(v => relation(v) === 'mine').length,
  team: ves.list.filter(v => relation(v) === 'team').length, shared: ves.list.filter(v => relation(v) === 'shared').length,
}))

const headers = [
  { title: 'Name', key: 'name' },
  { title: 'Owner', key: 'owner', value: (v: any) => v.owner.name ?? '' },
  { title: 'Plan', key: 'plan_id' },
  { title: 'Status', key: 'lifecycle', sortable: false },
  { title: 'Endpoints', key: 'endpoints', sortable: false },
  { title: 'Your role', key: 'my_role' },
  { title: 'Rental', key: 'period_end', value: (v: any) => v.rental?.period_end ?? 0 },
]
const planName = (id: string) => (id ? id[0]!.toUpperCase() + id.slice(1) : '—')
</script>
