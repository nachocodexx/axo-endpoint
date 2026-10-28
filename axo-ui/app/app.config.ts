// Navigation of axo-ui (the layout reads `axo.nav`; inside a VE it adds
// that VE's own section). Items are added as their pages are built.
export default defineAppConfig({
  axo: {
    home: '/dashboard',
    signup: true,
    nav: [
      { to: '/dashboard', icon: 'mdi-view-dashboard-outline', title: 'Dashboard' },
      { to: '/ves', icon: 'mdi-cube-outline', title: 'VEs' },
      { to: '/buckets', icon: 'mdi-database-outline', title: 'Buckets' },
      { to: '/teams', icon: 'mdi-account-group-outline', title: 'Teams' },
      { to: '/billing', icon: 'mdi-receipt-text-outline', title: 'Billing' },
    ],
  },
})
