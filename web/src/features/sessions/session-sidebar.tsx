import { PlusIcon, RefreshCwIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Spinner } from '@/components/ui/spinner'
import { useAsync } from '@/hooks/use-async'
import { api, describeError } from '@/lib/api'
import { dateTime } from '@/lib/format'
import { hrefFor, navigate } from '@/lib/route'
import { cn } from '@/lib/utils'

export function SessionSidebar({ activeId }: { activeId: string | null }) {
  const sessions = useAsync(() => api.listSessions(), 'sessions')
  const [creating, setCreating] = useState(false)
  const { reload } = sessions

  useEffect(() => {
    const timer = window.setInterval(reload, 5000)
    return () => window.clearInterval(timer)
  }, [reload])

  return (
    <aside className="flex min-h-0 flex-col border-r bg-sidebar">
      <div className="flex items-center gap-1 border-b px-3 py-2">
        <span className="flex-1 text-sm font-medium">会话</span>
        <Button size="icon-xs" variant="ghost" onClick={reload} aria-label="刷新会话列表">
          {sessions.loading ? <Spinner className="size-3" /> : <RefreshCwIcon />}
        </Button>
        <Button size="xs" onClick={() => setCreating(true)}>
          <PlusIcon />
          新建
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-1.5">
        {sessions.error && <p className="p-2 text-xs text-destructive">{sessions.error}</p>}
        {sessions.data?.length === 0 && (
          <p className="p-3 text-xs text-muted-foreground">还没有会话，点“新建”开始。</p>
        )}
        {sessions.data?.map((session) => (
          <a
            key={session.id}
            href={hrefFor({ page: 'chat', sessionId: session.id })}
            className={cn(
              'block rounded-md px-2 py-1.5 text-xs hover:bg-sidebar-accent',
              session.id === activeId && 'bg-sidebar-accent',
            )}
          >
            <div className="flex items-center gap-1.5">
              <span className="min-w-0 flex-1 truncate font-medium">{session.title || '未命名会话'}</span>
              {session.running && <span className="size-1.5 animate-pulse rounded-full bg-sky-500" title="运行中" />}
            </div>
            <div className="flex items-center gap-1 text-muted-foreground">
              <span className="font-mono">{session.id}</span>
              {session.topic && (
                <Badge variant="outline" className="h-4 px-1 text-[10px]">
                  {session.topic}
                </Badge>
              )}
            </div>
            <div className="text-[10px] text-muted-foreground">{dateTime(session.created_at)}</div>
          </a>
        ))}
      </div>
      <NewSessionDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(id) => {
          reload()
          navigate({ page: 'chat', sessionId: id })
        }}
      />
    </aside>
  )
}

export function NewSessionDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (id: string) => void
}) {
  const [title, setTitle] = useState('')
  const [topic, setTopic] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function create(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const session = await api.createSession({
        title: title.trim() || undefined,
        topic: topic.trim() || undefined,
      })
      onOpenChange(false)
      setTitle('')
      setTopic('')
      onCreated(session.id)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={create} className="space-y-4">
          <DialogHeader>
            <DialogTitle>新建会话</DialogTitle>
            <DialogDescription>
              topic 是长期记忆的键：同一主题的会话共享一份档案，出现在每一轮的 Turn Context 里。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="session-title">标题</Label>
            <Input id="session-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：产品系列文章" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="session-topic">topic（可选）</Label>
            <Input id="session-topic" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="例如：product-launch" />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={busy}>
              {busy && <Spinner />}
              创建
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
