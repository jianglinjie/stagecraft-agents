// The session event stream, reduced into what the console shows. Pure: tested in stream.test.ts.
//
// Server protocol (src/stagecraft/api/turns.py): turn_started, item_started, item_delta,
// sub_run, item_completed, turn_completed, turn_failed, state; resync after a trimmed replay.
// Every frame carries a per-session seq. A frame at or below the last applied seq is a duplicate
// (a replay overlapping what was already seen): it is logged as such and changes nothing.

import type { Plan } from '@/lib/types'

export const EVENT_TYPES = [
  'turn_started',
  'item_started',
  'item_delta',
  'sub_run',
  'item_completed',
  'turn_completed',
  'turn_failed',
  'state',
  'resync',
] as const

export interface StreamEvent {
  seq: number
  type: string
  created_at: string
  data: Record<string, unknown>
}

export interface LoggedEvent extends StreamEvent {
  duplicate: boolean
}

export interface SubRunCall {
  name: string
  arguments: unknown
  status: string | null
  output: unknown
}

export interface SubRunView {
  role: string
  payload: Record<string, unknown>
  calls: SubRunCall[]
}

export interface ToolCallView {
  kind: 'tool_call'
  itemId: string
  name: string
  arguments: unknown
  status: 'running' | 'ok' | 'error' | 'unknown'
  output?: unknown
  subRuns: SubRunView[]
}

export interface MessageView {
  kind: 'message'
  itemId: string
  text: string
  done: boolean
}

export type ItemView = ToolCallView | MessageView

export interface LiveTurn {
  turnId: string
  messageId?: string
  status: 'running' | 'completed' | 'failed'
  items: ItemView[]
  output?: string
  interrupted?: boolean
  error?: { code: string; message: string }
}

export interface StreamState {
  lastSeq: number
  log: LoggedEvent[]
  turns: Record<string, LiveTurn>
  turnOrder: string[]
  plan: Plan | null
  /** Whether a state event has arrived: until then the snapshot's plan is the truth. */
  planSeen: boolean
  running: boolean | null
  resyncs: number
  duplicates: number
}

export const LOG_LIMIT = 600

export function initialStreamState(): StreamState {
  return {
    lastSeq: 0,
    log: [],
    turns: {},
    turnOrder: [],
    plan: null,
    planSeen: false,
    running: null,
    resyncs: 0,
    duplicates: 0,
  }
}

export type StreamAction = { type: 'event'; event: StreamEvent } | { type: 'reset' }

export function streamReducer(state: StreamState, action: StreamAction): StreamState {
  return action.type === 'reset' ? initialStreamState() : applyEvent(state, action.event)
}

/** Parse one SSE frame's data (the JSON the server wrote after ``data:``). */
export function parseFrame(type: string, raw: string): StreamEvent | null {
  let payload: unknown
  try {
    payload = JSON.parse(raw)
  } catch {
    return null
  }
  if (typeof payload !== 'object' || payload === null) return null
  const { seq, created_at: createdAt, ...data } = payload as Record<string, unknown>
  if (typeof seq !== 'number') return null
  return { seq, type, created_at: String(createdAt ?? ''), data }
}

export function applyEvent(state: StreamState, event: StreamEvent): StreamState {
  if (event.type === 'resync') {
    return { ...state, log: appendLog(state.log, event, false), resyncs: state.resyncs + 1 }
  }
  if (event.seq <= state.lastSeq) {
    return {
      ...state,
      log: appendLog(state.log, event, true),
      duplicates: state.duplicates + 1,
    }
  }
  const next: StreamState = { ...state, lastSeq: event.seq, log: appendLog(state.log, event, false) }
  const data = event.data
  const turnId = typeof data.turn_id === 'string' ? data.turn_id : null

  switch (event.type) {
    case 'state':
      return {
        ...next,
        plan: (data.plan as Plan | null) ?? null,
        planSeen: true,
        running: Boolean(data.running),
      }
    case 'turn_started':
      return turnId
        ? updateTurn(next, turnId, (turn) => ({
            ...turn,
            messageId: String(data.message_id ?? ''),
            status: 'running',
          }))
        : next
    case 'item_started':
      return turnId ? updateTurn(next, turnId, (turn) => startItem(turn, data)) : next
    case 'item_delta':
      return turnId ? updateTurn(next, turnId, (turn) => appendDelta(turn, data)) : next
    case 'sub_run':
      return turnId ? updateTurn(next, turnId, (turn) => attachSubRun(turn, data)) : next
    case 'item_completed':
      return turnId ? updateTurn(next, turnId, (turn) => completeItem(turn, data)) : next
    case 'turn_completed':
      return turnId
        ? updateTurn(next, turnId, (turn) => ({
            ...turn,
            status: 'completed',
            output: String(data.output ?? ''),
            interrupted: Boolean(data.interrupted),
          }))
        : next
    case 'turn_failed':
      return turnId
        ? updateTurn(next, turnId, (turn) => ({
            ...turn,
            status: 'failed',
            error: { code: String(data.code ?? 'error'), message: String(data.message ?? '') },
          }))
        : next
    default:
      return next
  }
}

function appendLog(log: LoggedEvent[], event: StreamEvent, duplicate: boolean): LoggedEvent[] {
  const next = [...log, { ...event, duplicate }]
  return next.length > LOG_LIMIT ? next.slice(next.length - LOG_LIMIT) : next
}

function updateTurn(
  state: StreamState,
  turnId: string,
  change: (turn: LiveTurn) => LiveTurn,
): StreamState {
  const existing = state.turns[turnId]
  const turn = existing ?? { turnId, status: 'running', items: [] }
  return {
    ...state,
    turns: { ...state.turns, [turnId]: change(turn) },
    turnOrder: existing ? state.turnOrder : [...state.turnOrder, turnId],
  }
}

function startItem(turn: LiveTurn, data: Record<string, unknown>): LiveTurn {
  const itemId = String(data.item_id ?? '')
  if (turn.items.some((item) => item.itemId === itemId)) return turn
  const item: ItemView =
    data.kind === 'tool_call'
      ? {
          kind: 'tool_call',
          itemId,
          name: String(data.name ?? 'tool'),
          arguments: data.arguments,
          status: 'running',
          subRuns: [],
        }
      : { kind: 'message', itemId, text: '', done: false }
  return { ...turn, items: [...turn.items, item] }
}

function appendDelta(turn: LiveTurn, data: Record<string, unknown>): LiveTurn {
  const itemId = String(data.item_id ?? '')
  const delta = String(data.delta ?? '')
  const index = turn.items.findIndex((item) => item.itemId === itemId)
  if (index === -1) {
    return { ...turn, items: [...turn.items, { kind: 'message', itemId, text: delta, done: false }] }
  }
  const item = turn.items[index]
  if (item.kind !== 'message') return turn
  return { ...turn, items: replaceAt(turn.items, index, { ...item, text: item.text + delta }) }
}

/** A sub-run belongs to the latest unfinished dispatch for its role (it arrives just before
 * that dispatch completes). */
function attachSubRun(turn: LiveTurn, data: Record<string, unknown>): LiveTurn {
  const subRun: SubRunView = {
    role: String(data.role ?? ''),
    payload: (data.payload as Record<string, unknown>) ?? {},
    calls: Array.isArray(data.calls) ? (data.calls as SubRunCall[]) : [],
  }
  const dispatch = `dispatch_${subRun.role}`
  for (let index = turn.items.length - 1; index >= 0; index--) {
    const item = turn.items[index]
    if (item.kind === 'tool_call' && item.name === dispatch && item.status === 'running') {
      return {
        ...turn,
        items: replaceAt(turn.items, index, { ...item, subRuns: [...item.subRuns, subRun] }),
      }
    }
  }
  return turn
}

function completeItem(turn: LiveTurn, data: Record<string, unknown>): LiveTurn {
  const itemId = String(data.item_id ?? '')
  const index = turn.items.findIndex((item) => item.itemId === itemId)
  if (data.kind === 'message') {
    const text = String(data.text ?? '')
    if (index === -1) {
      return { ...turn, items: [...turn.items, { kind: 'message', itemId, text, done: true }] }
    }
    return { ...turn, items: replaceAt(turn.items, index, { kind: 'message', itemId, text, done: true }) }
  }
  const status = data.status === 'ok' ? 'ok' : data.status === 'error' ? 'error' : 'unknown'
  if (index === -1) {
    const item: ToolCallView = {
      kind: 'tool_call',
      itemId,
      name: String(data.name ?? 'tool'),
      arguments: undefined,
      status,
      output: data.output,
      subRuns: [],
    }
    return { ...turn, items: [...turn.items, item] }
  }
  const item = turn.items[index]
  if (item.kind !== 'tool_call') return turn
  return { ...turn, items: replaceAt(turn.items, index, { ...item, status, output: data.output }) }
}

function replaceAt<T>(items: T[], index: number, value: T): T[] {
  const copy = items.slice()
  copy[index] = value
  return copy
}

/** One-line description of an event for the event log. */
export function describeEvent(event: StreamEvent): string {
  const d = event.data
  switch (event.type) {
    case 'turn_started':
      return `turn ${d.turn_id} · message ${d.message_id}`
    case 'item_started':
      return d.kind === 'tool_call' ? `→ ${d.name}` : `message ${d.item_id}`
    case 'item_delta':
      return JSON.stringify(d.delta)
    case 'sub_run':
      return `${d.role} · ${Array.isArray(d.calls) ? d.calls.length : 0} tool call(s)`
    case 'item_completed':
      return d.kind === 'tool_call' ? `← ${d.name} ${d.status ?? ''}` : 'message done'
    case 'turn_completed':
      return d.interrupted ? 'completed · interrupted (waiting for answers)' : 'completed'
    case 'turn_failed':
      return `${d.code}: ${d.message}`
    case 'state': {
      const plan = d.plan as Plan | null
      const running = d.running ? 'running' : 'idle'
      return plan ? `${running} · ${plan.id} rev ${plan.revision}` : `${running} · no plan`
    }
    case 'resync':
      return `${d.reason} (log starts at seq ${d.from_seq})`
    default:
      return ''
  }
}
