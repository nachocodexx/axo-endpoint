<template>
  <v-main>
   <div class="axo-auth">
    <div class="axo-auth__brand d-none d-md-flex">
      <div class="axo-auth__glow" />
      <div class="axo-auth__nodes" aria-hidden="true">
        <span v-for="n in 14" :key="n" :style="node(n)" />
      </div>
      <div class="axo-auth__copy">
        <div class="d-flex align-center mb-10">
          <img src="/axo-logo.png" alt="" width="40" height="40">
          <span class="ml-3 text-h5 font-weight-bold text-white">{{ cfg.appName }}</span>
          <v-chip v-if="cfg.badge" size="small" class="ml-3 axo-gradient" variant="flat">{{ cfg.badge }}</v-chip>
        </div>
        <h1 class="axo-auth__title">{{ cfg.tagline }}</h1>
        <p class="text-body-1 mt-4 axo-auth__sub">{{ cfg.taglineText }}</p>
      </div>
    </div>
    <div class="axo-auth__form">
      <div class="d-flex justify-end pa-4">
        <v-btn icon variant="text" :aria-label="isDark ? 'Light theme' : 'Dark theme'" @click="toggle">
          <v-icon>{{ isDark ? 'mdi-weather-sunny' : 'mdi-weather-night' }}</v-icon>
        </v-btn>
      </div>
      <div class="flex-grow-1 d-flex align-center justify-center px-4 pb-12">
        <div style="width: 100%; max-width: 420px">
          <div class="d-flex d-md-none align-center justify-center mb-8">
            <img src="/axo-logo.png" alt="" width="36" height="36">
            <span class="ml-3 text-h5 font-weight-bold">{{ cfg.appName }}</span>
          </div>
          <slot />
        </div>
      </div>
    </div>
   </div>
    <AxoSnackbars />
  </v-main>
</template>

<script setup lang="ts">
// Sign-in / sign-up / password pages: the brand on the left (black, soft
// blue glow, drifting nodes), the form on the right.
const cfg = useAppConfig().axo
const { isDark, toggle } = useThemeSetting()

function node(n: number) {
  const x = (n * 37) % 100
  const y = (n * 61) % 100
  const size = 4 + (n % 4) * 3
  return { left: `${x}%`, top: `${y}%`, width: `${size}px`, height: `${size}px`,
    animationDelay: `${(n % 7) * -1.3}s`, animationDuration: `${9 + (n % 5) * 2}s` }
}
</script>

<style scoped>
.axo-auth { min-height: 100vh; display: grid; grid-template-columns: 1fr; }
@media (min-width: 960px) { .axo-auth { grid-template-columns: 1.1fr 1fr; } }
.axo-auth__brand { position: relative; overflow: hidden; background: #000; align-items: center; padding: 64px; min-height: 100vh; }
.axo-auth__glow {
  position: absolute; width: 900px; height: 900px; left: -260px; bottom: -420px; border-radius: 50%;
  background: radial-gradient(circle, rgba(0, 134, 255, 0.55) 0%, rgba(91, 91, 255, 0.25) 35%, transparent 70%);
  filter: blur(20px); animation: axo-breathe 9s ease-in-out infinite;
}
@keyframes axo-breathe { 50% { transform: scale(1.08) translate(30px, -20px); opacity: 0.85; } }
.axo-auth__nodes span {
  position: absolute; border-radius: 50%; background: rgba(0, 194, 255, 0.7);
  box-shadow: 0 0 12px rgba(0, 194, 255, 0.8); animation: axo-drift linear infinite;
}
@keyframes axo-drift { 0% { transform: translate(0, 0); opacity: 0; } 15% { opacity: 1; } 85% { opacity: 1; } 100% { transform: translate(40px, -120px); opacity: 0; } }
.axo-auth__copy { position: relative; max-width: 520px; }
.axo-auth__title { color: #fff; font-size: 2.6rem; line-height: 1.15; font-weight: 800; letter-spacing: -0.02em; }
.axo-auth__sub { color: rgba(255, 255, 255, 0.7); max-width: 440px; }
.axo-auth__form { display: flex; flex-direction: column; min-height: 100vh; background: rgb(var(--v-theme-background)); }
</style>
