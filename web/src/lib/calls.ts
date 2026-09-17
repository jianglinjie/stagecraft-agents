import { isRecord } from '@/lib/api'
import { clip, inlineArgs } from '@/lib/format'

/** The one fact worth reading without opening a call: route, stages, refs, state, error. */
export function callSummary(name: string, args: unknown, output: unknown): string {
  const a = isRecord(args) ? args : {}
  const o = isRecord(output) ? output : {}
  if (o.status === 'error') return `${o.code}: ${clip(String(o.message ?? ''), 120)}`
  switch (name) {
    case 'dispatch_router':
    case 'submit_route':
      return o.route ? `route=${o.route} · ${o.reason ?? ''}` : inlineArgs(a)
    case 'dispatch_planner': {
      if (!Object.keys(o).length) return inlineArgs({ plan_id: a.plan_id, answers: a.answers })
      const authored = (o.authored_stage_ids as string[] | undefined) ?? []
      const asked = (o.questions as string[] | undefined) ?? []
      return asked.length
        ? `interrupt · ${asked.length} question(s)`
        : `authored ${authored.join(', ') || 'nothing'} · rev ${o.revision}`
    }
    case 'dispatch_executor': {
      if (!Object.keys(o).length) return inlineArgs({ stage_id: a.stage_id, retry_ids: a.retry_ids })
      const refs = ((o.refs as { ref_id: string }[] | undefined) ?? []).map((r) => r.ref_id)
      const pending = (o.pending_items as string[] | undefined) ?? []
      return `${a.stage_id}: refs ${refs.join(', ') || 'none'}${pending.length ? ` · pending ${pending.join(', ')}` : ''}`
    }
    case 'plan_update_stage_state':
      return `${a.stage_id} → ${a.target}${a.user_confirmed ? ' (user_confirmed)' : ''}${o.revision !== undefined ? ` · rev ${o.revision}` : ''}`
    case 'plan_write_stage_contract':
      return o.stage_id
        ? `${o.stage_id}: ${clip(String(a.goal ?? ''), 50)}${Array.isArray(a.sources) && a.sources.length ? ` · sources ${a.sources.join(', ')}` : ''}`
        : clip(String(a.goal ?? ''), 60)
    case 'search_references': {
      const hits = (o.hits as { pointer: string }[] | undefined) ?? []
      return `“${a.query}” → ${hits.map((h) => h.pointer).join(', ') || 'no hits'}${o.mode ? ` (${o.mode})` : ''}`
    }
    case 'plan_create':
      return o.plan_id ? `${o.plan_id} · ${clip(String(a.objective ?? ''), 60)}` : inlineArgs(a)
    case 'plan_attach_runtime':
      return `${a.stage_id}: ${((a.refs as { ref_id: string }[] | undefined) ?? []).map((r) => r.ref_id).join(', ')}`
    case 'submit_plan':
    case 'submit_execution':
      return clip(String(o.summary ?? a.summary ?? ''), 90)
    default: {
      const ids = Object.entries(o)
        .filter(([key]) => key.endsWith('_id'))
        .map(([, value]) => value)
      return ids.length ? `${inlineArgs(a, 50)} → ${ids.join(', ')}` : inlineArgs(a)
    }
  }
}
