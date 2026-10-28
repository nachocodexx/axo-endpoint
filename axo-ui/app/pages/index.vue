<template><div /></template>

<script setup lang="ts">
// "/" → the dashboard (the auth middleware sends signed-out people to /login).
definePageMeta({ middleware: () => navigateTo('/dashboard', { redirectCode: 302 }) })
</script>
