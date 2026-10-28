<template>
  <NuxtLayout :name="forced ? 'auth' : 'default'">
    <div :style="forced ? '' : 'max-width: 520px'">
      <AxoPageHeader v-if="!forced" title="Change password" icon="mdi-lock-reset"
                     :crumbs="[{ title: 'Account', to: '/account' }, { title: 'Password', disabled: true }]" />
      <template v-else>
        <h2 class="text-h4 font-weight-bold mb-1">Set a new password</h2>
        <p class="text-body-2 axo-muted mb-8">Your account needs a new password before you go on.</p>
      </template>
      <v-card :class="forced ? 'pa-0' : 'pa-6'" :border="!forced" :flat="forced" color="transparent">
        <AxoPasswordForm :forced="forced" @changed="done" />
      </v-card>
    </div>
  </NuxtLayout>
</template>

<script setup lang="ts">
// The forced first change of the bootstrap Platform Admin (the API client
// and AxoShell send a PASSWORD_CHANGE_REQUIRED account here). Anyone else
// changes it on the Account page; this address still works for them.
definePageMeta({ layout: false })
useHead({ title: 'Change password · axo' })

const me = useMeStore()
// The layout is chosen once: after the change the flag turns false while
// the page is leaving, and swapping layouts mid-navigation breaks the DOM.
const forced = ref(!!me.me?.password_change_required)

onMounted(async () => {
  if (me.me) return
  try {
    forced.value = !!(await me.load())?.password_change_required
  } catch { /* the API client already sends a signed-out person to /login */ }
})

const done = () => navigateTo(forced.value ? useAppConfig().axo.home : '/account')
</script>
