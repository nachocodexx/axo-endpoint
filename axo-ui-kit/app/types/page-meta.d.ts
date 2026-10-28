declare module '#app' {
  interface PageMeta {
    /** false = no sign-in needed (see middleware/auth.global.ts). */
    auth?: boolean
  }
}
export {}
