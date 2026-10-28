export default defineAppConfig({
  axo: {
    badge: 'Kit',
    nav: [
      { to: '/', icon: 'mdi-view-dashboard-outline', title: 'Components' },
      { header: 'Help' },
      { to: '/errors', icon: 'mdi-book-alert-outline', title: 'Errors' },
    ],
  },
})
