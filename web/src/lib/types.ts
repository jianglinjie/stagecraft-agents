// Shapes returned by the Python API. They mirror the pydantic models and dataclasses in
// src/stagecraft (plan/model.py, assets/model.py, api/sessions.py, api/console.py, evals/runner.py).

export type Role = 'orchestrator' | 'router' | 'planner' | 'executor'
export const ROLES: Role[] = ['orchestrator', 'router', 'planner', 'executor']

// -- plan ------------------------------------------------------------------------------------

export type StageState = 'pending' | 'doing' | 'waiting_user' | 'blocked' | 'done' | 'omitted'
export type ReviewKind = 'plan_review' | 'result_review'

export interface WorkItem {
  id: string
  name: string
  instruction: string
}

export interface RuntimeRef {
  ref_id: string
  kind: string
  summary: string
  work_item_id: string | null
}

export interface StageContract {
  goal: string
  inputs: string[]
  work_items: WorkItem[]
  acceptance: string
  sources: string[]
  assets: string[]
}

export interface StageRuntime {
  refs: RuntimeRef[]
  notes: string | null
  attempts: number
}

export interface Stage {
  id: string
  order: number
  goal: string
  state: StageState
  contract: StageContract | null
  runtime: StageRuntime
  review_kind: ReviewKind | null
  blocked_reason: string | null
  questions: string[]
}

export interface Plan {
  id: string
  chat_id: string
  objective: string
  revision: number
  stages: Stage[]
  open_questions: string[]
}

// -- sessions ----------------------------------------------------------------------------------

export interface SessionRecord {
  id: string
  title: string | null
  topic: string | null
  created_at: string
}

export interface SessionListItem extends SessionRecord {
  running: boolean
}

export interface MessageRecord {
  id: string
  session_id: string
  role: 'user' | 'assistant'
  content: string
  client_message_id: string | null
  turn_id: string | null
  created_at: string
}

export interface TurnRecord {
  id: string
  session_id: string
  message_id: string
  status: 'running' | 'completed' | 'failed'
  output: string | null
  error: string | null
  started_at: string
  finished_at: string | null
}

export interface ArchiveRecord {
  archived_at: string
  reason: 'user_request' | 'replaced' | 'panel'
  message_id: string | null
  note: string | null
}

export interface Asset {
  id: string
  session_id: string
  name: string
  kind: string
  source_id: string
  summary: string
  ref_id: string | null
  created_at: string
  updated_at: string
  registered_by_message: string | null
  archive: ArchiveRecord | null
}

export interface SessionSnapshot extends SessionRecord {
  running: boolean
  messages: MessageRecord[]
  turns: TurnRecord[]
  plan: Plan | null
  assets: Asset[]
  archived_assets: Asset[]
  last_seq: number
}

export interface NewAsset {
  name: string
  kind: string
  source_id: string
  summary: string
}

export interface SendMessageBody {
  content: string
  client_message_id?: string
  attachments?: NewAsset[]
  archive_assets?: string[]
}

export interface TurnStarted {
  session_id: string
  message_id: string
  turn_id: string
  duplicate: boolean
  status: 'started' | 'duplicate'
}

export interface MemoryProfile {
  topic: string
  content: string
  revision: number
  updated_at: string
}

// -- console -----------------------------------------------------------------------------------

export type IndexMode = 'hybrid' | 'bm25_only' | 'empty'

export interface IndexStats {
  chunks: number
  mode: IndexMode
  vector_error: string | null
}

export interface ConsoleInfo {
  model: string
  references: IndexStats
  memory_threshold_tokens: number
  eval_sources: Record<string, string>
}

export interface JsonSchema {
  type?: string | string[]
  description?: string
  default?: unknown
  enum?: unknown[]
  items?: JsonSchema
  properties?: Record<string, JsonSchema>
  required?: string[]
  anyOf?: JsonSchema[]
  $ref?: string
  $defs?: Record<string, JsonSchema>
  minimum?: number
  maximum?: number
  title?: string
  [key: string]: unknown
}

export interface ToolInfo {
  name: string
  description: string
  parameters: JsonSchema
  roles: Role[]
}

export interface ToolCatalog {
  roles: Record<Role, string[]>
  tools: ToolInfo[]
}

export interface ReferenceHit {
  pointer: string
  title: string
  summary: string
  score: number
  matched_by: string[]
}

export interface ReferenceSearch {
  query: string
  mode: IndexMode
  stats: IndexStats
  hits: ReferenceHit[]
}

export interface MemoryItemPreview {
  kind: string
  text: string
}

export interface SessionContext {
  turn_context: string
  memory: {
    items: number
    estimated_tokens: number
    threshold_tokens: number
    compactions: number
    repair: { retired_tools: string[]; interrupted_calls: string[]; orphan_outputs: string[] }
    recent: MemoryItemPreview[]
  }
  topic: string | null
  profile: MemoryProfile | null
}

// -- evals -------------------------------------------------------------------------------------

export type CaseStatus = 'passed' | 'failed' | 'error'

export interface CheckResult {
  label: string
  passed: boolean
  detail: string
}

export interface JudgeResult {
  rubric: string
  scores: Record<string, number>
  rationale: string
  passed: boolean
}

export interface TurnSummary {
  user: string
  auto: boolean
  output: string
  error: string | null
  calls: Record<string, string[]>
  dispatches: string[]
  seconds: number
}

export interface Usage {
  requests: number
  input_tokens: number
  output_tokens: number
}

export interface CaseResult {
  case_id: string
  category: string
  description: string
  attempt: number
  status: CaseStatus
  checks: CheckResult[]
  judge: JudgeResult | null
  error: string | null
  retried: boolean
  turns: TurnSummary[]
  usage: Usage
  seconds: number
}

export interface SuiteMeta {
  label: string
  started_at: string
  model: string
  judge_model: string | null
  endpoint: string
  prompts: Record<string, string>
  rubric: string
  cases: number
  repeat: number
  concurrency: number
  seconds: number
  usage: Usage
  references: string | null
}

export interface SuiteResult {
  meta: SuiteMeta
  results: CaseResult[]
}

export interface EvalReportSummary {
  source: string
  label: string
  started_at: string
  model: string
  judge_model: string | null
  cases: number
  repeat: number
  runs: number
  passed: number
  failed: number
  errors: number
}

export interface EvalListing {
  sources: Record<string, string>
  reports: EvalReportSummary[]
}

export interface EvalReport {
  source: string
  label: string
  suite: SuiteResult
  markdown: string | null
}
