import { useCallback, useEffect, useReducer, useRef, useState } from 'react'

import { api, describeError } from '@/lib/api'
import {
  EVENT_TYPES,
  initialStreamState,
  parseFrame,
  streamReducer,
  type StreamEvent,
} from '@/lib/stream'
import type { SessionSnapshot } from '@/lib/types'

export type ConnectionState = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface ConnectionNote {
  at: string
  text: string
}

interface Handlers {
  onEvent(event: StreamEvent): void
  onStatus(state: ConnectionState): void
  onNote(text: string): void
  onReset(): void
}

const RETRY_MS = 2000
const BOUNDARIES = new Set(['turn_started', 'turn_completed', 'turn_failed', 'resync'])

/**
 * One session's SSE subscription. The position (the last seq applied) lives here, not in the URL:
 * every reconnect asks for ``?after=<last seq>``, so a dropped connection resumes with no gap, and
 * the reducer drops whatever a replay repeats. The browser's own EventSource retry is not used,
 * because it would repeat the URL's original ``after``.
 */
class StreamConnection {
  private source: EventSource | null = null
  private retry: number | null = null
  private lastSeq = 0
  private readonly sessionId: string
  private readonly handlers: Handlers

  constructor(sessionId: string, handlers: Handlers) {
    this.sessionId = sessionId
    this.handlers = handlers
  }

  get position(): number {
    return this.lastSeq
  }

  connect(after: number | undefined, reason: string): void {
    this.close()
    const url = api.eventsUrl(this.sessionId, after)
    const source = new EventSource(url)
    this.source = source
    this.handlers.onStatus('connecting')
    this.handlers.onNote(`${reason}：GET …/events${after === undefined ? '' : `?after=${after}`}`)
    source.onopen = () => this.source === source && this.handlers.onStatus('open')
    source.onerror = () => {
      if (this.source !== source) return
      this.close()
      this.handlers.onStatus('reconnecting')
      this.handlers.onNote(`连接中断，${RETRY_MS / 1000} 秒后从 seq ${this.lastSeq} 续传`)
      this.retry = window.setTimeout(() => void this.reconnect(), RETRY_MS)
    }
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (message) => {
        if (this.source !== source) return
        const event = parseFrame(type, (message as MessageEvent<string>).data)
        if (!event) return
        if (type !== 'resync') this.lastSeq = Math.max(this.lastSeq, event.seq)
        this.handlers.onEvent(event)
      })
    }
  }

  /** Resume from the last applied seq, unless the server's counter went backwards (it restarted
   * and its in-memory log starts again at 1): then start over, or nothing would ever arrive. */
  async reconnect(): Promise<void> {
    try {
      const snapshot = await api.session(this.sessionId)
      if (snapshot.last_seq < this.lastSeq) {
        this.handlers.onNote(
          `服务端 last_seq=${snapshot.last_seq} 小于本地 seq ${this.lastSeq}：服务端重启过，从头订阅`,
        )
        this.lastSeq = 0
        this.handlers.onReset()
      }
    } catch (err) {
      this.handlers.onNote(`读取快照失败（${describeError(err)}），照常续传`)
    }
    this.connect(this.lastSeq, '续传')
  }

  replayFrom(seq: number | undefined, reason: string): void {
    this.connect(seq, reason)
  }

  startOver(): void {
    this.lastSeq = 0
    this.handlers.onReset()
    this.connect(0, '清空并从头回放')
  }

  disconnect(): void {
    if (!this.source && this.retry === null) return
    this.close()
    this.handlers.onStatus('closed')
    this.handlers.onNote(`手动断开，停在 seq ${this.lastSeq}`)
  }

  dispose(): void {
    this.close()
  }

  private close(): void {
    this.source?.close()
    this.source = null
    if (this.retry !== null) {
      window.clearTimeout(this.retry)
      this.retry = null
    }
  }
}

/** A session as the console sees it: the stored snapshot plus the live event stream. Mount it
 * with ``key={sessionId}`` so each session starts from a clean state. */
export function useSessionLive(sessionId: string) {
  const [stream, dispatch] = useReducer(streamReducer, undefined, initialStreamState)
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null)
  const [snapshotError, setSnapshotError] = useState<string | null>(null)
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [notes, setNotes] = useState<ConnectionNote[]>([])
  const [contextVersion, setContextVersion] = useState(0)
  const controller = useRef<StreamConnection | null>(null)

  const refresh = useCallback(async () => {
    try {
      const next = await api.session(sessionId)
      setSnapshot(next)
      setSnapshotError(null)
    } catch (err) {
      setSnapshotError(describeError(err))
    }
  }, [sessionId])

  useEffect(() => {
    const connectionForSession = new StreamConnection(sessionId, {
      onEvent: (event) => {
        dispatch({ type: 'event', event })
        if (BOUNDARIES.has(event.type)) {
          void refresh()
          setContextVersion((v) => v + 1)
        }
      },
      onStatus: setConnection,
      onNote: (text) =>
        setNotes((prev) => [...prev.slice(-79), { at: new Date().toISOString(), text }]),
      onReset: () => dispatch({ type: 'reset' }),
    })
    controller.current = connectionForSession
    api.session(sessionId).then(setSnapshot, (err: unknown) => setSnapshotError(describeError(err)))
    connectionForSession.connect(0, '打开会话，回放服务端保留的事件')
    return () => {
      connectionForSession.dispose()
      controller.current = null
    }
  }, [sessionId, refresh])

  const controls = {
    disconnect: () => controller.current?.disconnect(),
    resume: () => controller.current?.reconnect(),
    replayFrom: (seq: number) =>
      controller.current?.replayFrom(seq, `从 seq ${seq} 之后回放（不清空，重复帧会被标出）`),
    tail: () =>
      controller.current?.replayFrom(undefined, '不带位置订阅（服务端只回放最近 50 条）'),
    startOver: () => controller.current?.startOver(),
  }

  return {
    stream,
    snapshot,
    snapshotError,
    connection,
    notes,
    refresh,
    controls,
    /** Bumps at every turn boundary: views derived from the server (Turn Context) reload on it. */
    contextVersion,
  }
}
