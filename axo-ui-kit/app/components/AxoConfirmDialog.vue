<template>
  <v-dialog :model-value="state.open" max-width="480" persistent @keydown.esc="answer(false)">
    <v-card class="pa-2">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon :color="state.danger ? 'error' : 'primary'">
          {{ state.danger ? 'mdi-alert-octagon-outline' : 'mdi-help-circle-outline' }}
        </v-icon>
        <span style="white-space: normal">{{ state.title }}</span>
      </v-card-title>
      <v-card-text>
        <p v-if="state.text" class="text-body-2 mb-3">{{ state.text }}</p>
        <ul v-if="state.consequences?.length" class="text-body-2 mb-3 pl-5">
          <li v-for="c in state.consequences" :key="c" class="mb-1">{{ c }}</li>
        </ul>
        <template v-if="state.typeToConfirm">
          <p class="text-body-2 mb-2">Type <strong class="axo-mono axo-selectable">{{ state.typeToConfirm }}</strong> to confirm.</p>
          <v-text-field v-model="typed" autofocus hide-details :placeholder="state.typeToConfirm" @keydown.enter="ok" />
        </template>
      </v-card-text>
      <v-card-actions class="px-4 pb-3">
        <v-spacer />
        <v-btn variant="text" @click="answer(false)">{{ state.cancelText ?? 'Cancel' }}</v-btn>
        <AxoBtn :look="state.danger ? 'danger' : 'gradient'" :disabled="!canConfirm" @click="ok">
          {{ state.confirmText ?? (state.danger ? 'Delete' : 'Confirm') }}
        </AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// The one confirmation dialog (see useConfirm). For the worst actions the
// person types the name to confirm, as the Control Plane also asks.
const { state, answer } = useConfirm()
const typed = ref('')
watch(() => state.value.open, () => { typed.value = '' })
const canConfirm = computed(() => !state.value.typeToConfirm || typed.value === state.value.typeToConfirm)
function ok() { if (canConfirm.value) answer(true) }
</script>
