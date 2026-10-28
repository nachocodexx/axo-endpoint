// Each app sets its own navigation in its app.config.ts (merged over this).
export default defineAppConfig({
  axo: {
    appName: 'Axo',
    /** Shown next to the logo (e.g. "Admin"). */
    badge: '',
    home: '/',
    /** Show "Create an account" on the sign-in page (axo-ui yes, admin no). */
    signup: false,
    tagline: 'Run your functions on your own cluster.',
    taglineText: 'Virtual environments, endpoints, functions and choreographies — with live metrics and clear costs.',
    nav: [] as Array<{ to?: string; icon?: string; title?: string; header?: string }>,
  },
})
