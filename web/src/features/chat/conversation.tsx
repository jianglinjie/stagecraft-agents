import { BotIcon, CircleAlertIcon, HourglassIcon, MessageSquareDashedIcon, UserIcon } from 'lucide-react'
import { useEffect, useRef } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Spinner } from '@/components/ui/spinner'
import { ToolCallCard } from '@/features/chat/tool-call-card'
import { clockTime } from '@/lib/format'
import type { StreamState } from '@/lib/stream'
import {
  buildTimeline,
  finalReply,
  interimMessages,
  turnError,
  turnStatus,
  type TurnEntry,
} from '@/lib/timeline'
import type { SessionSnapshot } from '@/lib/types'

export function Conversation({
  snapshot,
  stream,
}: {
  snapshot: SessionSnapshot | null
  stream: StreamState
}) {
  const entries = buildTimeline(snapshot, stream)
  const bottom = useRef<HTMLDivElement>(null)
  const lastSeq = stream.lastSeq
  const turnCount = entries.length

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [lastSeq, turnCount])

  if (!entries.length) {
    return (
      <Empty className="h-full">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <MessageSquareDashedIcon />
          </EmptyMedia>
          <EmptyTitle>还没有消息</EmptyTitle>
          <EmptyDescription>
            在下方发一条消息，或点一个示例。每条消息先落库并返回 202，agent 在后台运行，进度走 SSE。
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  return (
    <div className="space-y-6 px-5 py-4">
      {entries.map((entry) => (
        <TurnBlock key={entry.turnId} entry={entry} />
      ))}
      <div ref={bottom} />
    </div>
  )
}

function TurnBlock({ entry }: { entry: TurnEntry }) {
  const status = turnStatus(entry)
  const reply = finalReply(entry)
  const error = turnError(entry)
  const interim = new Set(interimMessages(entry).map((m) => m.itemId))
  const items = entry.live?.items ?? []

  return (
    <div className="space-y-2" data-turn={entry.turnId}>
      <div className="flex items-start gap-2">
        <div className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <UserIcon className="size-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="font-mono">{entry.turnId}</span>
            {entry.user?.client_message_id && (
              <Badge variant="outline" className="font-mono text-[10px]">
                client_message_id {entry.user.client_message_id}
              </Badge>
            )}
            <span>{clockTime(entry.user?.created_at ?? entry.record?.started_at)}</span>
          </div>
          <div className="mt-1 text-sm whitespace-pre-wrap">
            {entry.user?.content ?? <span className="text-muted-foreground">（消息尚未出现在快照中）</span>}
          </div>
        </div>
      </div>

      <div className="ml-8 space-y-1.5">
        {items.map((item) =>
          item.kind === 'tool_call' ? (
            <ToolCallCard key={item.itemId} call={item} />
          ) : interim.has(item.itemId) ? (
            <div key={item.itemId} className="rounded-lg border border-dashed px-2.5 py-1.5 text-xs whitespace-pre-wrap text-muted-foreground">
              {item.text}
              {!item.done && <span className="ml-0.5 animate-pulse">▍</span>}
            </div>
          ) : null,
        )}
        {entry.record && !entry.live && status !== 'running' && (
          <div className="text-[11px] text-muted-foreground">
            这一轮的工具调用已不在服务端的事件日志里（日志有上限，或服务端重启过），只剩存储的消息。
          </div>
        )}
      </div>

      {error && (
        <Alert variant="destructive" className="ml-8 w-auto">
          <CircleAlertIcon />
          <AlertTitle>turn_failed</AlertTitle>
          <AlertDescription className="font-mono text-xs break-all">{error}</AlertDescription>
        </Alert>
      )}

      {reply !== null && (
        <div className="flex items-start gap-2">
          <div className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-violet-500/15 text-violet-600 dark:text-violet-300">
            <BotIcon className="size-3.5" />
          </div>
          <div className="min-w-0 flex-1 rounded-lg bg-muted/60 px-3 py-2">
            {entry.live?.interrupted && (
              <Badge variant="outline" className="mb-1.5 border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300">
                <HourglassIcon />
                interrupt：本轮由代码结束，等待用户回答
              </Badge>
            )}
            <div className="text-sm whitespace-pre-wrap">{reply}</div>
          </div>
        </div>
      )}

      {status === 'running' && (
        <div className="ml-8 flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner className="size-3.5" />
          运行中…
        </div>
      )}
    </div>
  )
}
