<template><div /></template>

<script setup lang="ts">
// Older notice links: transfers live in the inbox.
definePageMeta({ redirect: { path: '/inbox', query: { tab: 'transfers' } } })
</script>
