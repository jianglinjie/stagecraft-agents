import type {
  ConsoleInfo,
  EvalListing,
  EvalReport,
  MemoryProfile,
  ReferenceSearch,
  SendMessageBody,
  SessionContext,
  SessionListItem,
  SessionRecord,
  SessionSnapshot,
  ToolCatalog,
  TurnStarted,
} from '@/lib/types'

// Vite proxies /api to the Python server (vite.config.ts); VITE_API_BASE overrides it.
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '/api'

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

// -- request log -------------------------------------------------------------------------------

export interface RequestRecord {
  id: number
  at: string
  method: string
  path: string
  status: number | null
  ms: number
  body?: unknown
  response?: unknown
  error?: string
}

type Listener = () => void

/** Every write the console sends, and every failed read: what testing 202/409/422 needs. */
class RequestLog {
  private records: RequestRecord[] = []
  private listeners = new Set<Listener>()
  private nextId = 1

  add(record: Omit<RequestRecord, 'id'>): void {
    this.records = [{ ...record, id: this.nextId++ }, ...this.records].slice(0, 200)
    this.listeners.forEach((listener) => listener())
  }

  clear(): void {
    this.records = []
    this.listeners.forEach((listener) => listener())
  }

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  snapshot = (): RequestRecord[] => this.records
}

export const requestLog = new RequestLog()

// -- requests ----------------------------------------------------------------------------------

async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const started = performance.now()
  const logged = method !== 'GET'
  let status: number | null = null
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    status = response.status
    const text = await response.text()
    const data: unknown = text ? parseJson(text) : null
    if (logged || !response.ok) {
      requestLog.add({
        at: new Date().toISOString(),
        method,
        path,
        status,
        ms: Math.round(performance.now() - started),
        body,
        response: data,
      })
    }
    if (!response.ok) {
      const detail = isRecord(data) && 'detail' in data ? data.detail : data
      throw new ApiError(response.status, detail)
    }
    return data as T
  } catch (err) {
    if (!(err instanceof ApiError)) {
      requestLog.add({
        at: new Date().toISOString(),
        method,
        path,
        status,
        ms: Math.round(performance.now() - started),
        body,
        error: err instanceof Error ? err.message : String(err),
      })
    }
    throw err
  }
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** A short human line for an API failure: status, code and message when the server sent them. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = err.detail
    if (isRecord(detail)) {
      const code = detail.code ? `${detail.code}: ` : ''
      return `HTTP ${err.status} ${code}${detail.message ?? JSON.stringify(detail)}`
    }
    if (Array.isArray(detail)) {
      return `HTTP ${err.status} ${detail.map((d) => (isRecord(d) ? d.msg : String(d))).join('; ')}`
    }
    return `HTTP ${err.status} ${detail ?? ''}`.trim()
  }
  return err instanceof Error ? err.message : String(err)
}

const q = encodeURIComponent

export const api = {
  health: () => request<{ status: string }>('GET', '/health'),
  info: () => request<ConsoleInfo>('GET', '/console/info'),

  listSessions: () => request<SessionListItem[]>('GET', '/sessions'),
  createSession: (body: { title?: string; topic?: string }) =>
    request<SessionRecord>('POST', '/sessions', body),
  session: (id: string) => request<SessionSnapshot>('GET', `/sessions/${q(id)}`),
  sendMessage: (id: string, body: SendMessageBody) =>
    request<TurnStarted>('POST', `/sessions/${q(id)}/messages`, body),
  extractMemory: (id: string) => request<MemoryProfile>('POST', `/sessions/${q(id)}/memory`),
  eventsUrl: (id: string, after?: number) =>
    `${API_BASE}/sessions/${q(id)}/events${after === undefined ? '' : `?after=${after}`}`,

  sessionContext: (id: string) =>
    request<SessionContext>('GET', `/console/sessions/${q(id)}/context`),
  tools: () => request<ToolCatalog>('GET', '/console/tools'),
  references: (query: string, topK: number) =>
    request<ReferenceSearch>('GET', `/console/references?q=${q(query)}&top_k=${topK}`),
  evals: () => request<EvalListing>('GET', '/console/evals'),
  evalReport: (source: string, label: string) =>
    request<EvalReport>('GET', `/console/evals/${q(source)}/${q(label)}`),
}
