import { CopyIcon, PlugIcon, PlugZapIcon, RotateCcwIcon, StepForwardIcon, UnplugIcon } from 'lucide-react'
import { useState } from 'react'

import { JsonBlock } from '@/components/json-block'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import type { ConnectionNote, ConnectionState } from '@/hooks/use-session-live'
import { clockTime } from '@/lib/format'
import { describeEvent, type LoggedEvent, type StreamState } from '@/lib/stream'
import { cn } from '@/lib/utils'

const CONNECTION_LABEL: Record<ConnectionState, { text: string; tone: string }> = {
  connecting: { text: '连接中', tone: 'text-sky-700 dark:text-sky-300' },
  open: { text: '已连接', tone: 'text-emerald-700 dark:text-emerald-300' },
  reconnecting: { text: '等待续传', tone: 'text-amber-700 dark:text-amber-300' },
  closed: { text: '已断开', tone: 'text-muted-foreground' },
}

const TYPE_TONE: Record<string, string> = {
  turn_started: 'text-violet-700 dark:text-violet-300',
  turn_completed: 'text-violet-700 dark:text-violet-300',
  turn_failed: 'text-destructive',
  state: 'text-sky-700 dark:text-sky-300',
  sub_run: 'text-amber-700 dark:text-amber-300',
  resync: 'text-destructive',
}

export interface StreamControls {
  disconnect: () => void
  resume: () => void
  replayFrom: (seq: number) => void
  tail: () => void
  startOver: () => void
}

export function EventsPanel({
  stream,
  connection,
  notes,
  controls,
  serverLastSeq,
}: {
  stream: StreamState
  connection: ConnectionState
  notes: ConnectionNote[]
  controls: StreamControls
  serverLastSeq: number | undefined
}) {
  const [hideDeltas, setHideDeltas] = useState(true)
  const [replaySeq, setReplaySeq] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const label = CONNECTION_LABEL[connection]
  const rows = stream.log
    .map((event, index) => ({ event, index }))
    .filter(({ event }) => !hideDeltas || event.type !== 'item_delta')
    .reverse()

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-2 border-b p-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className={cn('flex items-center gap-1 font-medium', label.tone)}>
            {connection === 'open' ? <PlugZapIcon className="size-3.5" /> : <PlugIcon className="size-3.5" />}
            {label.text}
          </span>
          <Badge variant="secondary" className="font-mono">本地 seq {stream.lastSeq}</Badge>
          {serverLastSeq !== undefined && (
            <Badge variant="outline" className="font-mono">服务端 last_seq {serverLastSeq}</Badge>
          )}
          <Badge variant="outline" className="font-mono">重复帧 {stream.duplicates}</Badge>
          <Badge variant="outline" className={cn('font-mono', stream.resyncs > 0 && 'border-red-500/40 text-destructive')}>
            resync {stream.resyncs}
          </Badge>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button size="xs" variant="outline" onClick={controls.disconnect} disabled={connection === 'closed'}>
            <UnplugIcon />
            断开
          </Button>
          <Button size="xs" variant="outline" onClick={controls.resume}>
            <StepForwardIcon />
            续传（after={stream.lastSeq}）
          </Button>
          <Button size="xs" variant="outline" onClick={controls.startOver}>
            <RotateCcwIcon />
            清空并从头回放
          </Button>
          <Button size="xs" variant="ghost" onClick={controls.tail}>
            不带位置订阅
          </Button>
          <form
            className="flex items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault()
              const seq = Number.parseInt(replaySeq, 10)
              if (Number.isFinite(seq) && seq >= 0) controls.replayFrom(seq)
            }}
          >
            <Input
              value={replaySeq}
              onChange={(e) => setReplaySeq(e.target.value)}
              placeholder="seq"
              inputMode="numeric"
              className="h-6 w-16 font-mono text-xs"
            />
            <Button size="xs" variant="ghost" type="submit">
              从此回放
            </Button>
          </form>
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          续传带 <code>?after=本地 seq</code>，服务端只补缺口；“从此回放”不清空本地状态，已见过的帧会标成重复并被忽略；
          落后超过服务端日志上限（300 条）时会先收到 resync，页面随即重新拉快照。
        </p>
        {notes.length > 0 && (
          <div className="max-h-20 overflow-auto rounded-md bg-muted/50 px-2 py-1 font-mono text-[11px] text-muted-foreground">
            {[...notes].reverse().map((note, index) => (
              <div key={index}>
                {clockTime(note.at)} {note.text}
              </div>
            ))}
          </div>
        )}
        <label className="flex items-center gap-2 text-xs">
          <Switch checked={hideDeltas} onCheckedChange={setHideDeltas} size="sm" />
          隐藏 item_delta（{stream.log.filter((e) => e.type === 'item_delta').length} 条）
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full text-xs">
          <tbody>
            {rows.map(({ event, index }) => (
              <EventRow
                key={index}
                event={event}
                open={selected === index}
                onToggle={() => setSelected(selected === index ? null : index)}
              />
            ))}
          </tbody>
        </table>
        {!rows.length && <p className="p-4 text-center text-xs text-muted-foreground">还没有事件</p>}
      </div>
    </div>
  )
}

function EventRow({ event, open, onToggle }: { event: LoggedEvent; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr
        onClick={onToggle}
        className={cn(
          'cursor-pointer border-b align-top hover:bg-muted/50',
          event.duplicate && 'opacity-50',
          event.type === 'resync' && 'bg-red-500/5',
        )}
      >
        <td className="w-12 py-1 pl-3 font-mono text-muted-foreground">{event.seq}</td>
        <td className={cn('w-28 py-1 font-mono', TYPE_TONE[event.type])}>{event.type}</td>
        <td className="py-1 pr-3 break-all">
          {event.duplicate && (
            <Badge variant="outline" className="mr-1 font-mono text-[10px]">
              <CopyIcon />
              重复，已忽略
            </Badge>
          )}
          {describeEvent(event)}
        </td>
        <td className="w-16 py-1 pr-3 text-right font-mono text-muted-foreground">{clockTime(event.created_at)}</td>
      </tr>
      {open && (
        <tr className="border-b">
          <td colSpan={4} className="px-3 py-2">
            <JsonBlock value={{ seq: event.seq, event: event.type, created_at: event.created_at, ...event.data }} />
          </td>
        </tr>
      )}
    </>
  )
}
