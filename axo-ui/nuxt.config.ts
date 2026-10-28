// axo-ui: the app people use. Everything shared with axo-admin-ui (theme,
// components, Control Plane client, sign-in) comes from the axo-ui-kit layer.
export default defineNuxtConfig({
  extends: ['axo-ui-kit'],
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },
  app: {
    head: {
      titleTemplate: '%s',
      title: 'axo',
      link: [{ rel: 'icon', type: 'image/x-icon', href: '/favicon.ico' }],
    },
    pageTransition: { name: 'axo-page', mode: 'out-in' },
  },
})
