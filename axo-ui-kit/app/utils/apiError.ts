import type { ApiErrorBody } from '../types/cp'

/** A failed Control Plane call: always has the catalog name and number. */
export class AxoApiError extends Error {
  readonly errorName: string
  readonly errorCode: number
  readonly status: number
  readonly details: Record<string, any>

  constructor(body: Partial<ApiErrorBody>, status = 0) {
    super(body.message || 'Something went wrong')
    this.name = 'AxoApiError'
    this.errorName = body.error || (status === 0 ? 'NETWORK_ERROR' : 'UNKNOWN_ERROR')
    this.errorCode = body.error_code ?? (this.errorName === 'NETWORK_ERROR' ? 9901 : this.errorName === 'UNKNOWN_ERROR' ? 9902 : 0)
    this.status = status
    this.details = body.details || {}
  }

  /** "NOT_FOUND · 9502" */
  get label(): string {
    return this.errorCode ? `${this.errorName} · ${this.errorCode}` : this.errorName
  }

  static from(err: any): AxoApiError {
    if (err instanceof AxoApiError) return err
    const status = err?.response?.status ?? err?.status ?? err?.statusCode ?? 0
    const data = err?.data ?? err?.response?._data
    if (data && typeof data === 'object' && typeof data.error === 'string') {
      return new AxoApiError(data, status)
    }
    if (data && typeof data === 'object' && typeof data.detail === 'string') {
      return new AxoApiError({ message: data.detail }, status)
    }
    if (status === 0) {
      return new AxoApiError({ error: 'NETWORK_ERROR', message: 'The platform could not be reached. Check your connection.' }, 0)
    }
    return new AxoApiError({ message: err?.message || 'Request failed' }, status)
  }
}
