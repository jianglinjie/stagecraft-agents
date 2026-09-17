import { describe, expect, it } from 'vitest'

import {
  applyEvent,
  initialStreamState,
  parseFrame,
  type StreamEvent,
  type StreamState,
} from '@/lib/stream'
import { buildTimeline, finalReply, interimMessages, turnStatus } from '@/lib/timeline'
import type { SessionSnapshot } from '@/lib/types'

let seq = 0
function ev(type: string, data: Record<string, unknown>, at?: number): StreamEvent {
  seq = at ?? seq + 1
  return { seq, type, created_at: '2026-09-17T00:00:00Z', data }
}

function run(events: StreamEvent[], from: StreamState = initialStreamState()): StreamState {
  return events.reduce(applyEvent, from)
}

const T = 't_1'

function workflowTurn(): StreamEvent[] {
  seq = 0
  return [
    ev('turn_started', { turn_id: T, message_id: 'm_1' }),
    ev('state', { running: true, plan: null }),
    ev('item_started', { turn_id: T, item_id: 'c1', kind: 'tool_call', name: 'dispatch_router', arguments: { request: 'x' } }),
    ev('sub_run', { turn_id: T, role: 'router', payload: { request: 'x' }, calls: [{ name: 'submit_route', arguments: {}, status: 'ok', output: {} }] }),
    ev('item_completed', { turn_id: T, item_id: 'c1', kind: 'tool_call', name: 'dispatch_router', status: 'ok', output: { route: 'workflow' } }),
    ev('item_started', { turn_id: T, item_id: 'msg', kind: 'message' }),
    ev('item_delta', { turn_id: T, item_id: 'msg', delta: 'Plan ' }),
    ev('item_delta', { turn_id: T, item_id: 'msg', delta: 'ready.' }),
    ev('item_completed', { turn_id: T, item_id: 'msg', kind: 'message', text: 'Plan ready.' }),
    ev('turn_completed', { turn_id: T, output: 'Plan ready.', interrupted: false }),
    ev('state', { running: false, plan: { id: 'plan_0001', revision: 3, stages: [] } }),
  ]
}

describe('applyEvent', () => {
  it('builds a turn from tool calls, the sub-run inside a dispatch, and streamed text', () => {
    const state = run(workflowTurn())
    const turn = state.turns[T]

    expect(state.lastSeq).toBe(11)
    expect(turn.status).toBe('completed')
    expect(turn.output).toBe('Plan ready.')
    const [call, message] = turn.items
    expect(call).toMatchObject({ kind: 'tool_call', name: 'dispatch_router', status: 'ok' })
    expect(call.kind === 'tool_call' && call.subRuns.map((s) => s.role)).toEqual(['router'])
    expect(message).toEqual({ kind: 'message', itemId: 'msg', text: 'Plan ready.', done: true })
    expect(state.running).toBe(false)
    expect(state.plan?.id).toBe('plan_0001')
  })

  it('logs a replayed frame as a duplicate and applies it once', () => {
    const events = workflowTurn()
    const once = run(events)
    const replayed = run(events.slice(5), once)

    expect(replayed.turns).toEqual(once.turns)
    expect(replayed.duplicates).toBe(6)
    expect(replayed.log.filter((e) => e.duplicate).map((e) => e.seq)).toEqual([6, 7, 8, 9, 10, 11])
  })

  it('counts a resync without moving the position', () => {
    seq = 0
    const state = run([ev('turn_started', { turn_id: T, message_id: 'm' }), ev('resync', { reason: 'trimmed', from_seq: 40 }, 1)])

    expect(state.lastSeq).toBe(1)
    expect(state.resyncs).toBe(1)
    expect(state.log.at(-1)).toMatchObject({ type: 'resync', duplicate: false })
  })

  it('records why a turn failed', () => {
    seq = 0
    const state = run([ev('turn_failed', { turn_id: T, code: 'agent_error', message: 'boom' })])

    expect(state.turns[T]).toMatchObject({ status: 'failed', error: { code: 'agent_error', message: 'boom' } })
  })

  it('keeps a sub-run off a dispatch that already finished', () => {
    seq = 0
    const state = run([
      ev('item_started', { turn_id: T, item_id: 'c1', kind: 'tool_call', name: 'dispatch_executor' }),
      ev('item_completed', { turn_id: T, item_id: 'c1', kind: 'tool_call', name: 'dispatch_executor', status: 'ok' }),
      ev('item_started', { turn_id: T, item_id: 'c2', kind: 'tool_call', name: 'dispatch_executor' }),
      ev('sub_run', { turn_id: T, role: 'executor', payload: { stage_id: 'stage_02' }, calls: [] }),
    ])
    const [first, second] = state.turns[T].items

    expect(first.kind === 'tool_call' && first.subRuns).toEqual([])
    expect(second.kind === 'tool_call' && second.subRuns[0].payload).toEqual({ stage_id: 'stage_02' })
  })
})

describe('parseFrame', () => {
  it('splits seq and time from the payload and rejects frames without a seq', () => {
    expect(parseFrame('state', '{"seq": 4, "created_at": "now", "running": true}')).toEqual({
      seq: 4,
      type: 'state',
      created_at: 'now',
      data: { running: true },
    })
    expect(parseFrame('state', '{"running": true}')).toBeNull()
    expect(parseFrame('state', 'not json')).toBeNull()
  })
})

describe('buildTimeline', () => {
  const snapshot = {
    turns: [
      { id: 't_0', status: 'completed', error: null },
      { id: T, status: 'running', error: null },
    ],
    messages: [
      { id: 'u0', role: 'user', content: 'old', turn_id: 't_0' },
      { id: 'a0', role: 'assistant', content: 'old reply', turn_id: 't_0' },
      { id: 'u1', role: 'user', content: 'new', turn_id: T },
    ],
  } as unknown as SessionSnapshot

  it('pairs stored messages with live detail and trusts the stream over a stale row', () => {
    const [old, current] = buildTimeline(snapshot, run(workflowTurn()))

    expect(old.live).toBeNull()
    expect(finalReply(old)).toBe('old reply')
    expect(current.user?.content).toBe('new')
    expect(turnStatus(current)).toBe('completed')
    expect(finalReply(current)).toBe('Plan ready.')
    expect(interimMessages(current)).toEqual([])
  })

  it('shows text still streaming as an interim message', () => {
    const partial = run(workflowTurn().slice(0, 7))
    const entry = buildTimeline(snapshot, partial)[1]

    expect(turnStatus(entry)).toBe('running')
    expect(finalReply(entry)).toBeNull()
    expect(interimMessages(entry).map((m) => m.text)).toEqual(['Plan '])
  })
})
