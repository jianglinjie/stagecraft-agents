import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { StageState } from '@/lib/types'

const STAGE_STYLES: Record<StageState, string> = {
  pending: 'border-border text-muted-foreground',
  waiting_user:
    'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300',
  doing: 'border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  blocked: 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300',
  done: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  omitted: 'border-border text-muted-foreground line-through',
}

export function StageStateBadge({ state, className }: { state: StageState; className?: string }) {
  return (
    <Badge variant="outline" className={cn('font-mono', STAGE_STYLES[state], className)}>
      {state}
    </Badge>
  )
}

const ROLE_STYLES: Record<string, string> = {
  orchestrator: 'border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300',
  router: 'border-cyan-500/40 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300',
  planner: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300',
  executor: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
}

export function RoleBadge({ role, className }: { role: string; className?: string }) {
  return (
    <Badge variant="outline" className={cn('font-mono', ROLE_STYLES[role], className)}>
      {role}
    </Badge>
  )
}

export function CallStatusBadge({ status }: { status: string | null | undefined }) {
  if (status === 'ok') {
    return (
      <Badge variant="outline" className="border-emerald-500/40 font-mono text-emerald-700 dark:text-emerald-300">
        ok
      </Badge>
    )
  }
  if (status === 'error') {
    return <Badge variant="destructive" className="font-mono">error</Badge>
  }
  if (status === 'running') {
    return <Badge variant="outline" className="font-mono text-sky-700 dark:text-sky-300">running</Badge>
  }
  return <Badge variant="outline" className="font-mono text-muted-foreground">{status ?? '—'}</Badge>
}

export function HttpStatusBadge({ status }: { status: number | null }) {
  const tone =
    status === null
      ? 'border-red-500/40 text-red-700 dark:text-red-300'
      : status >= 500
        ? 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300'
        : status >= 400
          ? 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300'
          : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  return (
    <Badge variant="outline" className={cn('font-mono', tone)}>
      {status ?? 'network'}
    </Badge>
  )
}
