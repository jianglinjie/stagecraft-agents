import { ChevronRightIcon, CornerDownRightIcon, SplitIcon, WrenchIcon } from 'lucide-react'
import { useState } from 'react'

import { JsonBlock } from '@/components/json-block'
import { CallStatusBadge, RoleBadge } from '@/components/status-badges'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Spinner } from '@/components/ui/spinner'
import { callSummary } from '@/lib/calls'
import type { SubRunCall, SubRunView, ToolCallView } from '@/lib/stream'
import { cn } from '@/lib/utils'

export function ToolCallCard({ call }: { call: ToolCallView }) {
  const [open, setOpen] = useState(false)
  const isDispatch = call.name.startsWith('dispatch_')
  const Icon = isDispatch ? SplitIcon : WrenchIcon
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border bg-card text-xs">
      <CollapsibleTrigger className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-muted/50">
        <ChevronRightIcon className={cn('size-3.5 shrink-0 transition-transform', open && 'rotate-90')} />
        <Icon className={cn('size-3.5 shrink-0', isDispatch ? 'text-violet-500' : 'text-muted-foreground')} />
        <span className="font-mono font-medium">{call.name}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">
          {callSummary(call.name, call.arguments, call.output)}
        </span>
        {call.status === 'running' ? <Spinner className="size-3.5" /> : <CallStatusBadge status={call.status} />}
      </CollapsibleTrigger>
      {call.subRuns.length > 0 && (
        <div className="space-y-1 border-t px-2.5 py-1.5">
          {call.subRuns.map((subRun, index) => (
            <SubRunLine key={index} subRun={subRun} />
          ))}
        </div>
      )}
      <CollapsibleContent className="space-y-2 border-t px-2.5 py-2">
        <Labeled label="arguments">
          <JsonBlock value={call.arguments ?? {}} />
        </Labeled>
        {call.output !== undefined && (
          <Labeled label="output">
            <JsonBlock value={call.output} />
          </Labeled>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}

function SubRunLine({ subRun }: { subRun: SubRunView }) {
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex w-full items-center gap-2 rounded text-left hover:bg-muted/50">
        <CornerDownRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
        <RoleBadge role={subRun.role} />
        <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground">
          {subRun.calls.map((c) => c.name).join(' → ') || 'no tool calls'}
        </span>
        <ChevronRightIcon className={cn('size-3.5 shrink-0 transition-transform', open && 'rotate-90')} />
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1.5 space-y-2 pl-5">
        <Labeled label="payload（子 agent 的全部输入）">
          <JsonBlock value={subRun.payload} />
        </Labeled>
        <div className="space-y-1">
          {subRun.calls.map((call, index) => (
            <SubRunCallRow key={index} call={call} />
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function SubRunCallRow({ call }: { call: SubRunCall }) {
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-md border">
      <CollapsibleTrigger className="flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-muted/50">
        <span className="font-mono">{call.name}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">
          {callSummary(call.name, call.arguments, call.output)}
        </span>
        <CallStatusBadge status={call.status} />
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-2 border-t p-2">
        <Labeled label="arguments">
          <JsonBlock value={call.arguments} />
        </Labeled>
        <Labeled label="output">
          <JsonBlock value={call.output} />
        </Labeled>
      </CollapsibleContent>
    </Collapsible>
  )
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="text-[11px] font-medium text-muted-foreground">{label}</div>
      {children}
    </div>
  )
}
