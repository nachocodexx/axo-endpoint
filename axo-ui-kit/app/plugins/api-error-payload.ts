import { AxoApiError } from '../utils/apiError'

// An AxoApiError kept in state during the server render (a store's `error`,
// useAsyncData) reaches the browser as itself instead of failing the page.
export default definePayloadPlugin(() => {
  definePayloadReducer('AxoApiError', (v: unknown) => v instanceof AxoApiError
    && { error: v.errorName, error_code: v.errorCode, message: v.message, details: v.details, status: v.status })
  definePayloadReviver('AxoApiError', (d: any) => new AxoApiError(d, d.status))
})
