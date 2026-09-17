import { MessagesSquareIcon, PlusIcon } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AssetsPanel } from '@/features/assets/assets-panel'
import { Composer } from '@/features/chat/composer'
import { Conversation } from '@/features/chat/conversation'
import { EventsPanel } from '@/features/events/events-panel'
import { MemoryPanel } from '@/features/memory/memory-panel'
import { PlanPanel } from '@/features/plan/plan-panel'
import { RequestsPanel } from '@/features/requests/requests-panel'
import { NewSessionDialog, SessionSidebar } from '@/features/sessions/session-sidebar'
import { useSessionLive } from '@/hooks/use-session-live'
import { navigate } from '@/lib/route'

export function ChatPage({ sessionId }: { sessionId: string | null }) {
  return (
    <div className="grid h-full min-h-0 grid-cols-[240px_minmax(0,1fr)]">
      <SessionSidebar activeId={sessionId} />
      {sessionId ? <SessionWorkspace key={sessionId} sessionId={sessionId} /> : <NoSession />}
    </div>
  )
}

function NoSession() {
  const [open, setOpen] = useState(false)
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <MessagesSquareIcon />
        </EmptyMedia>
        <EmptyTitle>选一个会话，或新建一个</EmptyTitle>
        <EmptyDescription>
          会话页左边是对话（每轮的工具调用、子 agent 的 payload 和调用都在里面），右边是 Plan、事件流、资产、记忆和请求日志。
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button onClick={() => setOpen(true)}>
          <PlusIcon />
          新建会话
        </Button>
      </EmptyContent>
      <NewSessionDialog
        open={open}
        onOpenChange={setOpen}
        onCreated={(id) => navigate({ page: 'chat', sessionId: id })}
      />
    </Empty>
  )
}

function SessionWorkspace({ sessionId }: { sessionId: string }) {
  const live = useSessionLive(sessionId)
  const [draft, setDraft] = useState('')
  const [archive, setArchive] = useState<string[]>([])
  const [tab, setTab] = useState('plan')
  const { snapshot, stream } = live

  const running = stream.running ?? snapshot?.running ?? false
  const plan = stream.planSeen ? stream.plan : (snapshot?.plan ?? null)
  const lastState = [...stream.log].reverse().find((event) => event.type === 'state' && !event.duplicate)
  const planSource = stream.planSeen && lastState ? `来自 state 事件 seq ${lastState.seq}` : '来自会话快照'
  const assetCount = snapshot?.assets.length ?? 0

  return (
    <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_minmax(400px,40%)]">
      <section className="flex min-h-0 flex-col border-r">
        <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
          <h2 className="text-sm font-medium">{snapshot?.title || '未命名会话'}</h2>
          <span className="font-mono text-xs text-muted-foreground">{sessionId}</span>
          {snapshot?.topic && <Badge variant="outline">topic {snapshot.topic}</Badge>}
          <span className="ml-auto flex items-center gap-1.5 text-xs">
            {running ? (
              <Badge variant="outline" className="border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300">
                running · 持有租约
              </Badge>
            ) : (
              <Badge variant="outline" className="text-muted-foreground">idle</Badge>
            )}
          </span>
        </header>
        {live.snapshotError && <p className="px-4 py-2 text-xs text-destructive">{live.snapshotError}</p>}
        <div className="min-h-0 flex-1 overflow-auto">
          <Conversation snapshot={snapshot} stream={stream} />
        </div>
        <Composer
          sessionId={sessionId}
          running={running}
          archive={archive}
          onArchiveChange={setArchive}
          onSent={() => void live.refresh()}
          text={draft}
          onTextChange={setDraft}
        />
      </section>

      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-col gap-0">
        <div className="border-b px-3 py-2">
          <TabsList className="w-full">
            <TabsTrigger value="plan">Plan</TabsTrigger>
            <TabsTrigger value="events">
              事件 <span className="font-mono text-[10px] text-muted-foreground">{stream.lastSeq}</span>
            </TabsTrigger>
            <TabsTrigger value="assets">
              资产 <span className="font-mono text-[10px] text-muted-foreground">{assetCount}</span>
            </TabsTrigger>
            <TabsTrigger value="memory">记忆</TabsTrigger>
            <TabsTrigger value="requests">请求</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="plan" className="min-h-0 overflow-auto">
          <PlanPanel
            plan={plan}
            source={planSource}
            onDraft={(text) => setDraft(text)}
          />
        </TabsContent>
        <TabsContent value="events" className="min-h-0">
          <EventsPanel
            stream={stream}
            connection={live.connection}
            notes={live.notes}
            controls={live.controls}
            serverLastSeq={snapshot?.last_seq}
          />
        </TabsContent>
        <TabsContent value="assets" className="min-h-0 overflow-auto">
          <AssetsPanel
            assets={snapshot?.assets ?? []}
            archived={snapshot?.archived_assets ?? []}
            selected={archive}
            onSelectedChange={setArchive}
          />
        </TabsContent>
        <TabsContent value="memory" className="min-h-0 overflow-auto">
          {tab === 'memory' && (
            <MemoryPanel
              sessionId={sessionId}
              topic={snapshot?.topic ?? null}
              version={live.contextVersion}
              running={running}
            />
          )}
        </TabsContent>
        <TabsContent value="requests" className="min-h-0">
          <RequestsPanel filter={sessionId} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
