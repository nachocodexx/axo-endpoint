<template>
  <!-- The drawer and app bar stay black in both themes (by design). -->
  <v-navigation-drawer v-model="drawer" :permanent="!mobile" :temporary="mobile" :rail="rail && !mobile" rail-width="72"
                       color="#000000" width="248" class="axo-drawer">
    <NuxtLink :to="home" class="axo-brand d-flex align-center pa-4">
      <img src="/axo-logo.png" alt="axo" width="32" height="32">
      <span v-if="!rail || mobile" class="ml-3 text-white font-weight-bold text-h6">Axo</span>
      <v-chip v-if="badge && (!rail || mobile)" size="x-small" class="ml-2 axo-gradient" variant="flat">{{ badge }}</v-chip>
    </NuxtLink>

    <v-list bg-color="transparent" base-color="white" color="primary" nav density="compact" class="px-2 pt-0">
      <template v-for="item in nav" :key="item.to ?? item.header">
        <v-list-subheader v-if="item.header && (!rail || mobile)" class="text-grey-darken-1 text-uppercase text-caption">
          {{ item.header }}
        </v-list-subheader>
        <v-list-item v-else-if="item.to" :to="item.to" :prepend-icon="item.icon" :title="item.title" rounded="lg"
                     :active="item.active" class="axo-nav-item">
          <v-tooltip v-if="rail && !mobile" activator="parent" location="end">{{ item.title }}</v-tooltip>
        </v-list-item>
      </template>
    </v-list>

    <v-btn v-if="!mobile" icon size="26" elevation="2" color="white" class="axo-rail-toggle"
           :aria-label="rail ? 'Expand navigation' : 'Collapse navigation'" @click="rail = !rail">
      <v-icon :icon="rail ? 'mdi-chevron-right' : 'mdi-chevron-left'" size="18" color="#000000" />
    </v-btn>
  </v-navigation-drawer>

  <v-app-bar color="#000000" flat density="comfortable">
    <v-app-bar-nav-icon v-if="mobile" color="white" @click="drawer = !drawer" />
    <slot name="bar" />
    <v-spacer />
    <slot name="bar-end" />
    <v-tooltip :text="live.connected.value ? 'Live updates on' : 'Reconnecting…'">
      <template #activator="{ props: tip }">
        <v-icon v-bind="tip" size="10" :color="live.connected.value ? 'success' : 'warning'"
                :class="['mr-3', { 'axo-pulse': !live.connected.value }]">mdi-circle</v-icon>
      </template>
    </v-tooltip>
    <v-btn icon variant="text" color="white" :aria-label="isDark ? 'Light theme' : 'Dark theme'" @click="toggle">
      <v-icon>{{ isDark ? 'mdi-weather-sunny' : 'mdi-weather-night' }}</v-icon>
    </v-btn>
    <div class="text-white"><AxoNotificationBell /></div>
    <v-menu location="bottom end">
      <template #activator="{ props: menu }">
        <div class="d-flex align-center axo-user ml-2 mr-4" v-bind="menu">
          <v-avatar size="34" class="axo-gradient mr-2"><span class="text-caption font-weight-bold">{{ me.initials || '·' }}</span></v-avatar>
          <span class="text-white text-body-2 d-none d-sm-inline">{{ me.displayName }}</span>
          <v-icon icon="mdi-chevron-down" color="white" size="18" />
        </div>
      </template>
      <v-list density="compact" min-width="220" rounded="lg">
        <v-list-item v-if="me.me" :title="me.displayName" :subtitle="me.me.email || me.me.username" />
        <v-divider class="my-1" />
        <v-list-item prepend-icon="mdi-account-circle-outline" title="Account" to="/account" />
        <slot name="user-menu" />
        <v-list-item prepend-icon="mdi-help-circle-outline" title="Help & FAQ" to="/help" />
        <v-list-item prepend-icon="mdi-cog-outline" title="Settings" to="/account/settings" />
        <v-divider class="my-1" />
        <v-list-item prepend-icon="mdi-logout" title="Sign out" @click="me.signOut()" />
      </v-list>
    </v-menu>
  </v-app-bar>

  <v-main class="axo-main">
    <v-container fluid class="axo-content pa-4 pa-md-6">
      <v-defaults-provider :defaults="tableDefaults">
        <slot />
      </v-defaults-provider>
    </v-container>
  </v-main>

  <AxoSnackbars />
  <AxoConfirmDialog />
</template>

<script setup lang="ts">
import { useDisplay } from 'vuetify'

// The frame of every signed-in page: black navigation drawer (collapses to
// icons), black top bar (live dot, theme, bell, account), the page, and the
// app-wide snackbars + confirmation dialog. Starts the live channel.
// Slots: `bar` (left of the top bar), `bar-end` (right, before the live
// dot), `user-menu` (the app's own account menu items, between Account and
// Help & FAQ; every app has a /help page).
// `active`: set by the app when the link alone can't tell (left out = the router decides).
export interface NavItem { to?: string; icon?: string; title?: string; header?: string; active?: boolean }
withDefaults(defineProps<{ nav: NavItem[]; home?: string; badge?: string }>(), { home: '/' })

const me = useMeStore()
const live = useLive()
const { isDark, toggle } = useThemeSetting()
const { mobile } = useDisplay()
// The person's table spacing, for every table on every page.
const { settings } = useSettings()
const tableDefaults = computed(() => ({ VTable: { density: settings.value.density }, VDataTable: { density: settings.value.density } }))
const drawer = ref(true)
// On a phone the drawer floats over the page: closed until asked for, and
// closed again after following a link.
watch(mobile, (m) => { drawer.value = !m }, { immediate: true })
watch(() => useRoute().fullPath, () => { if (mobile.value) drawer.value = false })
const rail = useCookie<boolean>('axo_rail', { default: () => false })

const session = useAuthSession()
onMounted(async () => {
  if (!session.value) return
  live.start()
  try {
    await me.load()
    if (me.me?.password_change_required && useRoute().path !== '/account/password') await navigateTo('/account/password')
  } catch (err) { useNotifier().error(err, "Couldn't load your account") }
})
</script>

<style scoped>
.axo-drawer { position: relative; }
/* The drawer scrolls when its menu is taller than the screen: a thin, faint
   bar that only shows while the pointer is over the drawer. */
.axo-drawer :deep(.v-navigation-drawer__content) { scrollbar-width: thin; scrollbar-color: transparent transparent; }
.axo-drawer:hover :deep(.v-navigation-drawer__content) { scrollbar-color: rgba(255, 255, 255, 0.28) transparent; }
.axo-drawer :deep(.v-navigation-drawer__content)::-webkit-scrollbar { width: 6px; }
.axo-drawer :deep(.v-navigation-drawer__content)::-webkit-scrollbar-thumb { background: transparent; border-radius: 3px; }
.axo-drawer:hover :deep(.v-navigation-drawer__content)::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.28); }
.axo-drawer :deep(.axo-nav-item) { margin-bottom: 2px; }
.axo-drawer :deep(.axo-nav-item.v-list-item--active) {
  background: linear-gradient(90deg, rgba(0, 134, 255, 0.35), rgba(91, 91, 255, 0.12));
  color: #fff !important;
}
.axo-brand { text-decoration: none; }
.axo-main { min-height: 100vh; }
.axo-content { max-width: 1600px; margin: 0 auto; }
.axo-user { cursor: pointer; }
.axo-rail-toggle { position: absolute; top: 50%; right: -13px; transform: translateY(-50%); z-index: 10; }
</style>
