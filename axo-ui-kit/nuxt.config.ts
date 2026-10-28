import { fileURLToPath } from 'node:url'

// The shared layer of axo-ui and axo-admin-ui (`extends: ['axo-ui-kit']`).
// Everything both apps need lives here: the axo theme (light + dark), the
// Control Plane client and session, snackbars + bell, confirmations, help
// icons, tables, status chips and chart wrappers.
export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  modules: ['vuetify-nuxt-module', '@pinia/nuxt'],
  css: [fileURLToPath(new URL('./app/assets/css/kit.css', import.meta.url))],
  build: { transpile: ['echarts', 'vue-echarts', 'zrender'] },
  runtimeConfig: {
    // The Control Plane as the UI's own server reaches it while rendering a
    // page (NUXT_API_BASE_SERVER), when that differs from the browser's
    // address, e.g. http://axo-control-plane:9000 inside docker-compose.
    // Empty = apiBase.
    apiBaseServer: '',
    public: {
      // The Control Plane: the one front door (AXO_CP_HTTP_PORT, default 9000).
      apiBase: process.env.NUXT_PUBLIC_API_BASE || 'http://localhost:9000',
    },
  },
  vuetify: {
    vuetifyOptions: {
      icons: { defaultSet: 'mdi', sets: ['mdi'] },
      // Two named themes, light the default. The drawer and app bar are not
      // theme-driven: they stay black in both (see AxoShell.vue). #0086FF is
      // the one brand color (primary = secondary = accent); the gradients in
      // kit.css run from it to `brand-2` / `brand-3`.
      theme: {
        defaultTheme: 'axoLight',
        themes: {
          axoLight: {
            dark: false,
            colors: {
              background: '#F4F5F8',
              surface: '#FFFFFF',
              'surface-variant': '#E7E9EE',
              // Vuetify keeps its own default otherwise (light text): tooltips unreadable
              'on-surface-variant': '#1B1C1F',
              primary: '#0086FF',
              secondary: '#0086FF',
              accent: '#0086FF',
              'brand-2': '#00C2FF',
              'brand-3': '#5B5BFF',
              success: '#12B886',
              warning: '#F59F00',
              error: '#E5484D',
              info: '#0086FF',
              muted: '#8A8F98',
            },
          },
          axoDark: {
            dark: true,
            colors: {
              background: '#0B0B0D',
              surface: '#1B1C1F',
              'surface-variant': '#2A2B2F',
              'on-surface-variant': '#E7E9EE',
              primary: '#0086FF',
              secondary: '#0086FF',
              accent: '#0086FF',
              'brand-2': '#00C2FF',
              'brand-3': '#7C7CFF',
              success: '#20C997',
              warning: '#FAB005',
              error: '#FF6369',
              info: '#3DA2FF',
              muted: '#9BA1A6',
            },
          },
        },
      },
      defaults: {
        VCard: { rounded: 'lg', elevation: 0, border: true },
        VBtn: { rounded: 'lg', style: 'text-transform: none; letter-spacing: 0' },
        VTextField: { variant: 'outlined', density: 'comfortable', color: 'primary' },
        VSelect: { variant: 'outlined', density: 'comfortable', color: 'primary' },
        VAutocomplete: { variant: 'outlined', density: 'comfortable', color: 'primary' },
        VTextarea: { variant: 'outlined', density: 'comfortable', color: 'primary' },
        VChip: { rounded: 'lg' },
        VTooltip: { location: 'top' },
      },
    },
  },
})
