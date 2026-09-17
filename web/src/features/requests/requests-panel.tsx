import { useState, useSyncExternalStore } from 'react'

import { JsonBlock } from '@/components/json-block'
import { HttpStatusBadge } from '@/components/status-badges'
import { Button } from '@/components/ui/button'
import { requestLog } from '@/lib/api'
import { clockTime } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Every write the console sent (and every failed read), newest first. */
export function RequestsPanel({ filter }: { filter?: string }) {
  const records = useSyncExternalStore(requestLog.subscribe, requestLog.snapshot)
  const [open, setOpen] = useState<number | null>(null)
  const shown = filter ? records.filter((record) => record.path.includes(filter)) : records

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2 text-xs">
        <span className="text-muted-foreground">
          写请求与失败的读请求：看 202 started / 202 duplicate / 409 / 422 的区别
        </span>
        <Button size="xs" variant="ghost" onClick={() => requestLog.clear()}>
          清空
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {shown.map((record) => (
          <div key={record.id} className="border-b text-xs">
            <button
              className={cn('flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/50', open === record.id && 'bg-muted/50')}
              onClick={() => setOpen(open === record.id ? null : record.id)}
            >
              <HttpStatusBadge status={record.status} />
              <span className="font-mono">{record.method}</span>
              <span className="min-w-0 flex-1 truncate font-mono">{record.path}</span>
              <span className="font-mono text-muted-foreground">{record.ms}ms</span>
              <span className="font-mono text-muted-foreground">{clockTime(record.at)}</span>
            </button>
            {open === record.id && (
              <div className="grid gap-2 px-3 pb-2">
                {record.body !== undefined && (
                  <div>
                    <div className="mb-1 text-[11px] text-muted-foreground">request</div>
                    <JsonBlock value={record.body} />
                  </div>
                )}
                <div>
                  <div className="mb-1 text-[11px] text-muted-foreground">response</div>
                  <JsonBlock value={record.error ?? record.response} />
                </div>
              </div>
            )}
          </div>
        ))}
        {!shown.length && <p className="p-4 text-center text-xs text-muted-foreground">还没有请求</p>}
      </div>
    </div>
  )
}
