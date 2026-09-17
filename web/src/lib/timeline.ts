// A conversation is rebuilt from two sources: the snapshot (what is stored: messages and turn
// rows) and the live stream (what happened inside each turn: tool calls, sub-runs, deltas). The
// stream only covers what the server's bounded event log still holds, so a stored turn can have
// no live detail.

import type { LiveTurn, MessageView } from '@/lib/stream'
import type { StreamState } from '@/lib/stream'
import type { MessageRecord, SessionSnapshot, TurnRecord } from '@/lib/types'

export type TurnStatus = 'running' | 'completed' | 'failed'

export interface TurnEntry {
  turnId: string
  user: MessageRecord | null
  assistant: MessageRecord | null
  record: TurnRecord | null
  live: LiveTurn | null
}

export function buildTimeline(snapshot: SessionSnapshot | null, stream: StreamState): TurnEntry[] {
  const users = new Map<string, MessageRecord>()
  const assistants = new Map<string, MessageRecord>()
  for (const message of snapshot?.messages ?? []) {
    if (!message.turn_id) continue
    ;(message.role === 'user' ? users : assistants).set(message.turn_id, message)
  }
  const entries: TurnEntry[] = []
  const seen = new Set<string>()
  for (const record of snapshot?.turns ?? []) {
    seen.add(record.id)
    entries.push({
      turnId: record.id,
      user: users.get(record.id) ?? null,
      assistant: assistants.get(record.id) ?? null,
      record,
      live: stream.turns[record.id] ?? null,
    })
  }
  for (const turnId of stream.turnOrder) {
    if (seen.has(turnId)) continue
    entries.push({ turnId, user: null, assistant: null, record: null, live: stream.turns[turnId] })
  }
  return entries
}

/** The stream knows first; a stored row that still says running is only stale. */
export function turnStatus(entry: TurnEntry): TurnStatus {
  if (entry.record && entry.record.status !== 'running') return entry.record.status
  return entry.live?.status ?? entry.record?.status ?? 'running'
}

/** The reply for a turn: the stored assistant message, else the stream's final output. */
export function finalReply(entry: TurnEntry): string | null {
  if (entry.assistant) return entry.assistant.content
  if (entry.live?.status === 'completed') return entry.live.output ?? ''
  return null
}

export function turnError(entry: TurnEntry): string | null {
  if (entry.live?.error) return `${entry.live.error.code}: ${entry.live.error.message}`
  if (entry.record?.status === 'failed') return entry.record.error ?? 'failed'
  return null
}

/** Messages streamed inside the turn that are not the final reply (text next to tool calls,
 * or the reply itself while it is still being written). */
export function interimMessages(entry: TurnEntry): MessageView[] {
  const reply = finalReply(entry)?.trim()
  return (entry.live?.items ?? []).filter(
    (item): item is MessageView => item.kind === 'message' && item.text.trim() !== reply,
  )
}
