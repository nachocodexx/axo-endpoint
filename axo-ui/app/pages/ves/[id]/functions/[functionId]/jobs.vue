<template>
  <JobsList :function-id="functionId" />
</template>

<script setup lang="ts">
// The function's jobs: the VE's jobs list, kept to this function.
const { functionId } = useCurrentFunction()
</script>
