import { ChevronRightIcon, CircleHelpIcon, ClipboardListIcon, LinkIcon } from 'lucide-react'
import { useState } from 'react'

import { StageStateBadge } from '@/components/status-badges'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { navigate, pointerQuery } from '@/lib/route'
import type { Plan, Stage, StageState } from '@/lib/types'
import { cn } from '@/lib/utils'

const FLOW: StageState[] = ['pending', 'waiting_user', 'doing', 'done']

export function PlanPanel({
  plan,
  source,
  onDraft,
}: {
  plan: Plan | null
  /** Where the plan came from: the last ``state`` event, or the snapshot before any arrived. */
  source: string
  onDraft: (text: string) => void
}) {
  if (!plan) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ClipboardListIcon />
          </EmptyMedia>
          <EmptyTitle>还没有计划</EmptyTitle>
          <EmptyDescription>
            router 判为 workflow 后，orchestrator 会创建 plan，planner 写 stage 契约。direct 路径不建计划。
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  const stages = [...plan.stages].sort((a, b) => a.order - b.order)
  const waiting = stages.find((stage) => stage.state === 'waiting_user')
  const blocked = stages.find((stage) => stage.state === 'blocked')

  return (
    <div className="space-y-3 p-3">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-sm font-medium">{plan.id}</span>
          <Badge variant="secondary" className="font-mono">rev {plan.revision}</Badge>
          <span className="text-[11px] text-muted-foreground">{source}</span>
        </div>
        <p className="text-sm">{plan.objective}</p>
      </div>

      <StateLane stages={stages} />

      {plan.open_questions.length > 0 && (
        <Alert className="border-amber-500/40 bg-amber-500/5">
          <CircleHelpIcon />
          <AlertTitle>planner 在等用户回答（open_questions）</AlertTitle>
          <AlertDescription>
            <ol className="list-decimal space-y-0.5 pl-4">
              {plan.open_questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ol>
            <Button size="xs" variant="outline" className="mt-2" onClick={() => onDraft('developers, playful tone, as a PDF')}>
              填入一个回答
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {waiting && (
        <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/5 px-2.5 py-2 text-xs">
          <span>
            <span className="font-mono">{waiting.id}</span> 在 {waiting.review_kind}：没有用户明确批准，状态机里没有去 doing 的边。
          </span>
          <Button size="xs" variant="outline" onClick={() => onDraft('Approved. Please continue.')}>
            填入“批准”
          </Button>
          <Button size="xs" variant="ghost" onClick={() => onDraft('skip')}>
            填入“跳过”
          </Button>
        </div>
      )}
      {blocked && !waiting && (
        <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-red-500/40 bg-red-500/5 px-2.5 py-2 text-xs">
          <span>
            <span className="font-mono">{blocked.id}</span> blocked：{blocked.blocked_reason}
          </span>
          <Button size="xs" variant="outline" onClick={() => onDraft('skip')}>
            填入“跳过”
          </Button>
        </div>
      )}

      <div className="space-y-2">
        {stages.map((stage) => (
          <StageCard key={stage.id} stage={stage} />
        ))}
      </div>
    </div>
  )
}

function StateLane({ stages }: { stages: Stage[] }) {
  const counts = new Map<StageState, number>()
  for (const stage of stages) counts.set(stage.state, (counts.get(stage.state) ?? 0) + 1)
  const side: StageState[] = ['blocked', 'omitted']
  return (
    <div className="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
      {FLOW.map((state, index) => (
        <span key={state} className="flex items-center gap-1">
          {index > 0 && <ChevronRightIcon className="size-3" />}
          <span className={cn(!counts.get(state) && 'opacity-50')}>
            <StageStateBadge state={state} /> ×{counts.get(state) ?? 0}
          </span>
        </span>
      ))}
      <span className="mx-1 text-border">|</span>
      {side.map((state) => (
        <span key={state} className={cn(!counts.get(state) && 'opacity-50')}>
          <StageStateBadge state={state} /> ×{counts.get(state) ?? 0}
        </span>
      ))}
    </div>
  )
}

function StageCard({ stage }: { stage: Stage }) {
  const [open, setOpen] = useState(stage.state === 'waiting_user' || stage.state === 'blocked')
  const contract = stage.contract
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border bg-card">
      <CollapsibleTrigger className="flex w-full items-center gap-2 px-2.5 py-2 text-left hover:bg-muted/50">
        <ChevronRightIcon className={cn('size-3.5 shrink-0 transition-transform', open && 'rotate-90')} />
        <span className="font-mono text-xs text-muted-foreground">{stage.order}.</span>
        <span className="font-mono text-xs">{stage.id}</span>
        <span className="min-w-0 flex-1 truncate text-sm">{stage.goal}</span>
        {stage.review_kind && (
          <Badge variant="outline" className="font-mono text-[10px]">{stage.review_kind}</Badge>
        )}
        <StageStateBadge state={stage.state} />
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-3 border-t px-3 py-2.5 text-xs">
        {stage.blocked_reason && <p className="text-destructive">blocked_reason：{stage.blocked_reason}</p>}
        {stage.questions.length > 0 && <p>questions：{stage.questions.join(' | ')}</p>}

        <section className="space-y-1.5">
          <h4 className="font-medium">contract <span className="font-normal text-muted-foreground">· planner 写</span></h4>
          {contract ? (
            <>
              {contract.inputs.length > 0 && (
                <p>
                  inputs：<span className="font-mono">{contract.inputs.join(', ')}</span>
                </p>
              )}
              <ul className="space-y-1">
                {contract.work_items.map((item) => (
                  <li key={item.id} className="rounded-md bg-muted/50 px-2 py-1">
                    <span className="font-mono">{item.id}</span> <span className="font-medium">{item.name}</span>
                    <span className="text-muted-foreground"> · {item.instruction}</span>
                  </li>
                ))}
              </ul>
              {contract.acceptance && <p>acceptance：{contract.acceptance}</p>}
              <div className="flex flex-wrap items-center gap-1">
                <span>sources：</span>
                {contract.sources.length ? (
                  contract.sources.map((pointer) => (
                    <Badge
                      key={pointer}
                      asChild
                      variant="outline"
                      className="cursor-pointer font-mono hover:bg-muted"
                    >
                      <button
                        onClick={() => navigate({ page: 'references', query: pointerQuery(pointer) })}
                        title="到检索页查这条规范"
                      >
                        <LinkIcon />
                        {pointer}
                      </button>
                    </Badge>
                  ))
                ) : (
                  <span className="text-muted-foreground">无</span>
                )}
              </div>
              {contract.assets.length > 0 && (
                <p>
                  assets：<span className="font-mono">{contract.assets.join(', ')}</span>
                </p>
              )}
            </>
          ) : (
            <p className="text-muted-foreground">还没有契约：不能进入评审或开始。</p>
          )}
        </section>

        <section className="space-y-1.5">
          <h4 className="font-medium">
            runtime <span className="font-normal text-muted-foreground">· executor 写，attempts {stage.runtime.attempts}</span>
          </h4>
          {stage.runtime.refs.length ? (
            <ul className="space-y-1">
              {stage.runtime.refs.map((ref) => (
                <li key={`${ref.ref_id}-${ref.work_item_id}`} className="flex flex-wrap gap-1.5">
                  <span className="font-mono">{ref.ref_id}</span>
                  <Badge variant="secondary">{ref.kind}</Badge>
                  {ref.work_item_id && <span className="font-mono text-muted-foreground">← {ref.work_item_id}</span>}
                  <span className="text-muted-foreground">{ref.summary}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground">还没有产出：不能标记 done。</p>
          )}
          {stage.runtime.notes && <p className="text-muted-foreground">notes：{stage.runtime.notes}</p>}
        </section>
      </CollapsibleContent>
    </Collapsible>
  )
}
