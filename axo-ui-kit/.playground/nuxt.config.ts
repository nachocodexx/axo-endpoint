// `pnpm --filter axo-ui-kit dev`: every kit component on one page.
export default defineNuxtConfig({
  extends: ['..'],
  compatibilityDate: '2025-07-15',
  devtools: { enabled: false },
})
