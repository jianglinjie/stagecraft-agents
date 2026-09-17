import { BrainIcon, RefreshCwIcon } from 'lucide-react'
import { useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Spinner } from '@/components/ui/spinner'
import { useAsync } from '@/hooks/use-async'
import { api, describeError } from '@/lib/api'
import { dateTime } from '@/lib/format'
import { cn } from '@/lib/utils'

export function MemoryPanel({
  sessionId,
  topic,
  version,
  running,
}: {
  sessionId: string
  topic: string | null
  /** Changes at each turn boundary, so the context is re-read when the server state moved. */
  version: number
  running: boolean
}) {
  const context = useAsync(() => api.sessionContext(sessionId), `${sessionId}:${version}`)
  const [extracting, setExtracting] = useState(false)
  const [extractError, setExtractError] = useState<string | null>(null)
  const data = context.data
  const memory = data?.memory

  async function extract() {
    setExtracting(true)
    setExtractError(null)
    try {
      await api.extractMemory(sessionId)
      context.reload()
    } catch (err) {
      setExtractError(describeError(err))
    } finally {
      setExtracting(false)
    }
  }

  return (
    <div className="space-y-4 p-3 text-xs">
      <div className="flex items-center justify-between">
        <p className="text-muted-foreground">三层记忆，按寿命由短到长。</p>
        <Button size="xs" variant="ghost" onClick={context.reload} disabled={context.loading}>
          {context.loading ? <Spinner className="size-3" /> : <RefreshCwIcon />}
          刷新
        </Button>
      </div>
      {context.error && (
        <Alert variant="destructive">
          <AlertDescription>{context.error}</AlertDescription>
        </Alert>
      )}

      <section className="space-y-1.5">
        <h3 className="text-sm font-medium">
          1. Turn Context <span className="text-xs font-normal text-muted-foreground">每次调用模型前现场组装，放在 instructions 里，不进会话历史</span>
        </h3>
        <pre className="max-h-64 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
          {data?.turn_context ?? '…'}
        </pre>
      </section>

      <section className="space-y-1.5">
        <h3 className="text-sm font-medium">
          2. 会话记忆 <span className="text-xs font-normal text-muted-foreground">orchestrator 的历史，SQLite 持久化，超阈值压缩</span>
        </h3>
        {memory && (
          <>
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="secondary">{memory.items} 条</Badge>
              <Badge variant="outline" className="font-mono">
                ≈{memory.estimated_tokens} / {memory.threshold_tokens} tokens
              </Badge>
              <Badge variant="outline">压缩 {memory.compactions} 次</Badge>
            </div>
            <Progress value={Math.min(100, (memory.estimated_tokens / memory.threshold_tokens) * 100)} />
            <p className="text-muted-foreground">
              下一轮开始前超过阈值就把较早的轮次交给压缩器写成摘要；阈值用 STAGECRAFT_MEMORY_THRESHOLD_TOKENS 调低即可在页面上触发。
            </p>
            {(memory.repair.retired_tools.length > 0 || memory.repair.interrupted_calls.length > 0) && (
              <Alert>
                <AlertDescription>
                  读时修复：退役工具 {memory.repair.retired_tools.join(', ') || '无'}；未返回的调用{' '}
                  {memory.repair.interrupted_calls.length}
                </AlertDescription>
              </Alert>
            )}
            <ul className="space-y-1">
              {memory.recent.map((item, index) => (
                <li key={index} className="flex gap-2 rounded-md border px-2 py-1">
                  <span
                    className={cn(
                      'w-20 shrink-0 font-mono',
                      item.kind === 'user' && 'text-violet-700 dark:text-violet-300',
                      item.kind === 'assistant' && 'text-emerald-700 dark:text-emerald-300',
                    )}
                  >
                    {item.kind}
                  </span>
                  <span className="min-w-0 break-all">{item.text}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="space-y-1.5">
        <h3 className="text-sm font-medium">
          3. 长期记忆 <span className="text-xs font-normal text-muted-foreground">按主题一份档案，整体重写而不是追加</span>
        </h3>
        {topic ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">topic：{topic}</Badge>
              <Button size="xs" variant="outline" onClick={extract} disabled={extracting || running}>
                {extracting ? <Spinner className="size-3" /> : <BrainIcon />}
                从本会话重写档案
              </Button>
              {running && <span className="text-muted-foreground">有 turn 在运行时接口返回 409</span>}
            </div>
            {extractError && <p className="text-destructive">{extractError}</p>}
            {data?.profile ? (
              <div className="space-y-1 rounded-md border p-2">
                <p className="text-muted-foreground">
                  revision {data.profile.revision} · {dateTime(data.profile.updated_at)}
                </p>
                <pre className="font-mono text-[11px] whitespace-pre-wrap">{data.profile.content}</pre>
              </div>
            ) : (
              <p className="text-muted-foreground">这个主题还没有档案。</p>
            )}
          </>
        ) : (
          <p className="text-muted-foreground">这个会话没有 topic。新建会话时填 topic，才能提取和读取长期记忆。</p>
        )}
      </section>
    </div>
  )
}
