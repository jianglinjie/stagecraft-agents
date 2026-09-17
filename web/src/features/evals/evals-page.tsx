import { ChevronRightIcon, FlaskConicalIcon, GitCompareIcon } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAsync } from '@/hooks/use-async'
import { api } from '@/lib/api'
import { compare, flakyCases, judgeMeans, percent, rate, tallies } from '@/lib/evals'
import { clip } from '@/lib/format'
import type { CaseResult, EvalReportSummary, SuiteResult } from '@/lib/types'
import { cn } from '@/lib/utils'

const keyOf = (r: { source: string; label: string }) => `${r.source}/${r.label}`

export function EvalsPage() {
  const listing = useAsync(() => api.evals(), 'evals')
  const reports = listing.data?.reports ?? []
  const [selected, setSelected] = useState<string | null>(null)
  const current = reports.find((r) => keyOf(r) === selected) ?? reports[0]

  return (
    <div className="grid h-full min-h-0 grid-cols-[280px_minmax(0,1fr)]">
      <aside className="min-h-0 overflow-auto border-r p-3">
        <h2 className="mb-2 text-sm font-medium">评测报告</h2>
        {listing.error && <p className="text-xs text-destructive">{listing.error}</p>}
        {reports.map((report) => (
          <button
            key={keyOf(report)}
            onClick={() => setSelected(keyOf(report))}
            className={cn(
              'mb-1 block w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted',
              current && keyOf(current) === keyOf(report) && 'bg-muted',
            )}
          >
            <div className="flex items-center gap-1.5">
              <span className="flex-1 truncate font-mono font-medium">{report.label}</span>
              <Badge variant="outline">{report.source}</Badge>
            </div>
            <div className="text-muted-foreground">
              {report.started_at} · {report.model}
            </div>
            <div className="text-muted-foreground">
              {report.passed}/{report.runs - report.errors} 通过 · {report.errors} error · ×{report.repeat}
            </div>
          </button>
        ))}
        {listing.data && (
          <p className="mt-3 text-[11px] text-muted-foreground">
            读取目录：
            {Object.entries(listing.data.sources).map(([name, path]) => (
              <span key={name} className="block font-mono">
                {name} → {path}
              </span>
            ))}
          </p>
        )}
      </aside>
      <main className="min-h-0 overflow-auto p-6">
        {listing.loading && !listing.data && <Skeleton className="h-64 w-full" />}
        {listing.data && !reports.length && (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <FlaskConicalIcon />
              </EmptyMedia>
              <EmptyTitle>还没有评测报告</EmptyTitle>
              <EmptyDescription>
                用真实端点跑 <code className="font-mono">uv run --env-file .env python evals/run.py --label baseline</code>
                ，结果 JSON 写到 .data/evals/，这里就会列出来；提交进 docs/evals/ 的报告也会出现。
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
        {current && (
          <Tabs defaultValue="report">
            <TabsList>
              <TabsTrigger value="report">报告</TabsTrigger>
              <TabsTrigger value="compare" disabled={reports.length < 2}>
                <GitCompareIcon />
                对比
              </TabsTrigger>
            </TabsList>
            <TabsContent value="report">
              <ReportView key={keyOf(current)} summary={current} />
            </TabsContent>
            <TabsContent value="compare">
              <CompareView reports={reports} />
            </TabsContent>
          </Tabs>
        )}
      </main>
    </div>
  )
}

function ReportView({ summary }: { summary: EvalReportSummary }) {
  const report = useAsync(() => api.evalReport(summary.source, summary.label), keyOf(summary))
  const suite = report.data?.suite
  if (report.error) return <p className="text-sm text-destructive">{report.error}</p>
  if (!suite) return <Skeleton className="h-64 w-full" />
  const { meta, results } = suite
  const flaky = meta.repeat > 1 ? flakyCases(results) : []
  const means = judgeMeans(results)
  const failures = results
    .filter((r) => r.status !== 'passed')
    .sort((a, b) => `${a.category}${a.case_id}${a.attempt}`.localeCompare(`${b.category}${b.case_id}${b.attempt}`))

  return (
    <div className="space-y-5">
      <div className="space-y-1 text-sm">
        <h1 className="font-mono text-lg font-semibold">{meta.label}</h1>
        <p className="text-muted-foreground">
          {meta.started_at} · 被测 <span className="font-mono">{meta.model}</span> @ {meta.endpoint} · judge{' '}
          {meta.judge_model ? <span className="font-mono">{meta.judge_model}</span> : '关闭'} ({meta.rubric})
        </p>
        <p className="text-muted-foreground">
          {meta.cases} 条用例 ×{meta.repeat} · 并发 {meta.concurrency} · {Math.round(meta.seconds)}s ·{' '}
          {meta.usage.requests} 次请求 · {meta.usage.input_tokens.toLocaleString()} 输入 /{' '}
          {meta.usage.output_tokens.toLocaleString()} 输出 token
        </p>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(meta.prompts).map(([role, fingerprint]) => (
            <Badge key={role} variant="outline" className="font-mono">
              {role} {fingerprint}
            </Badge>
          ))}
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>分类</TableHead>
            <TableHead className="text-right">runs</TableHead>
            <TableHead className="text-right">passed</TableHead>
            <TableHead className="text-right">failed</TableHead>
            <TableHead className="text-right">errors</TableHead>
            <TableHead className="w-48">通过率（不计 error）</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tallies(results).map(([name, t]) => (
            <TableRow key={name} className={cn(name === 'all' && 'font-medium')}>
              <TableCell className="font-mono">{name}</TableCell>
              <TableCell className="text-right">{t.runs}</TableCell>
              <TableCell className="text-right">{t.passed}</TableCell>
              <TableCell className="text-right">{t.failed}</TableCell>
              <TableCell className="text-right">{t.errors}</TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  <Progress value={rate(t) * 100} className="h-1.5" />
                  <span className="w-10 text-right font-mono text-xs">{percent(rate(t))}</span>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {means.length > 0 && (
        <section className="space-y-1.5">
          <h2 className="text-sm font-medium">Judge 均分</h2>
          <div className="flex flex-wrap gap-1.5">
            {means.map((m) => (
              <Badge key={m.criterion} variant="secondary" className="font-mono">
                {m.criterion} {m.mean.toFixed(2)}
              </Badge>
            ))}
          </div>
        </section>
      )}

      {flaky.length > 0 && (
        <section className="space-y-1.5">
          <h2 className="text-sm font-medium">不稳定用例</h2>
          <div className="flex flex-wrap gap-1.5">
            {flaky.map((c) => (
              <Badge key={c.caseId} variant="outline" className="font-mono">
                {c.caseId} {c.passed}/{c.runs}
              </Badge>
            ))}
          </div>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-sm font-medium">失败与错误（{failures.length}）</h2>
        {failures.map((r) => (
          <FailureCard key={`${r.case_id}-${r.attempt}`} result={r} repeat={meta.repeat} />
        ))}
        {!failures.length && <p className="text-sm text-muted-foreground">没有。</p>}
      </section>
    </div>
  )
}

function FailureCard({ result, repeat }: { result: CaseResult; repeat: number }) {
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border text-xs">
      <CollapsibleTrigger className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/50">
        <ChevronRightIcon className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
        <span className="font-mono font-medium">{result.case_id}</span>
        {repeat > 1 && <span className="text-muted-foreground">run {result.attempt}</span>}
        <Badge variant="outline">{result.category}</Badge>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">
          {result.error ?? result.checks.filter((c) => !c.passed).map((c) => c.label).join('; ')}
        </span>
        <Badge variant={result.status === 'failed' ? 'destructive' : 'outline'}>{result.status}</Badge>
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-3 border-t px-3 py-2">
        <p>{result.description}</p>
        {result.error && (
          <p className="text-destructive">
            error：{result.error}
            {result.retried && '（已重试一次）'}
          </p>
        )}
        {result.checks
          .filter((c) => !c.passed)
          .map((c) => (
            <p key={c.label}>
              <span className="font-medium text-destructive">✗ {c.label}</span>
              <span className="text-muted-foreground"> · 看到：{c.detail}</span>
            </p>
          ))}
        {result.judge && !result.judge.passed && (
          <p>
            judge：{Object.entries(result.judge.scores).map(([k, v]) => `${k} ${v}`).join(', ')}。{result.judge.rationale}
          </p>
        )}
        {result.turns.map((turn, index) => (
          <div key={index} className="space-y-0.5 rounded-md bg-muted/50 p-2">
            <p>
              <span className="font-medium">第 {index + 1} 轮（{turn.auto ? '自动批准' : '用户'}）</span>：{clip(turn.user, 200)}
            </p>
            {Object.entries(turn.calls).map(([role, calls]) => (
              <p key={role} className="font-mono">
                {role}: {calls.join(' → ')}
              </p>
            ))}
            {turn.dispatches.map((d, i) => (
              <p key={i} className="font-mono text-muted-foreground">
                sent {clip(d, 240)}
              </p>
            ))}
            {turn.error && <p className="text-destructive">error: {turn.error}</p>}
            <p className="text-muted-foreground">reply: {clip(turn.output, 400)}</p>
          </div>
        ))}
      </CollapsibleContent>
    </Collapsible>
  )
}

function CompareView({ reports }: { reports: EvalReportSummary[] }) {
  const [before, setBefore] = useState(keyOf(reports[reports.length > 1 ? 1 : 0]))
  const [after, setAfter] = useState(keyOf(reports[0]))
  const find = (key: string) => reports.find((r) => keyOf(r) === key)!
  const loaded = useAsync(
    () => Promise.all([before, after].map((key) => api.evalReport(find(key).source, find(key).label))),
    `${before}|${after}`,
  )
  const pair = loaded.data?.map((r) => r.suite) as [SuiteResult, SuiteResult] | undefined
  const comparison = pair ? compare(pair[0], pair[1]) : null

  const picker = (value: string, onChange: (v: string) => void) => (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-56">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {reports.map((r) => (
          <SelectItem key={keyOf(r)} value={keyOf(r)}>
            {keyOf(r)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        之前 {picker(before, setBefore)} 之后 {picker(after, setAfter)}
      </div>
      {loaded.error && <p className="text-sm text-destructive">{loaded.error}</p>}
      {comparison && (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>分类</TableHead>
                <TableHead className="text-right">之前</TableHead>
                <TableHead className="text-right">之后</TableHead>
                <TableHead className="text-right">变化</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {comparison.categories.map((c) => (
                <TableRow key={c.name}>
                  <TableCell className="font-mono">{c.name}</TableCell>
                  <TableCell className="text-right font-mono">
                    {c.before.passed}/{c.before.runs - c.before.errors} ({percent(rate(c.before))})
                  </TableCell>
                  <TableCell className="text-right font-mono">
                    {c.after.passed}/{c.after.runs - c.after.errors} ({percent(rate(c.after))})
                  </TableCell>
                  <TableCell
                    className={cn(
                      'text-right font-mono',
                      c.delta > 0 && 'text-emerald-600',
                      c.delta < 0 && 'text-destructive',
                    )}
                  >
                    {c.delta >= 0 ? '+' : ''}
                    {Math.round(c.delta)} pt
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <p className="text-sm">
            转为通过（{comparison.nowPassing.length}）：
            <span className="font-mono">{comparison.nowPassing.join(', ') || '无'}</span>
          </p>
          <p className="text-sm">
            转为失败（{comparison.nowFailing.length}）：
            <span className="font-mono">{comparison.nowFailing.join(', ') || '无'}</span>
          </p>
          <p className="text-xs text-muted-foreground">
            每条用例跑多次时总分差几个点仍可能是噪声，主要看哪些用例翻转。
          </p>
        </>
      )}
    </div>
  )
}
