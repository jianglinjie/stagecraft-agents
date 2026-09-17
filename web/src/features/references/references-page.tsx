import { BookOpenTextIcon, CopyIcon, SearchIcon, TriangleAlertIcon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'
import { useAsync } from '@/hooks/use-async'
import { api } from '@/lib/api'
import { navigate } from '@/lib/route'
import type { IndexMode } from '@/lib/types'
import { cn } from '@/lib/utils'

const EXAMPLES = [
  'playful tone headlines for a developer audience',
  'PDF datasheet specification table',
  'captions and length for a short video',
  'people new to the topic',
]

const MODE_NOTE: Record<IndexMode, string> = {
  hybrid: 'BM25 与向量两路召回，按名次做 RRF 融合',
  bm25_only: '没有可用的 embeddings 端点：只跑 BM25，改写式查询可能找不到',
  empty: '语料为空：检索什么都返回不了，contract 的 sources 一律被拒',
}

export function ReferencesPage({ query }: { query: string | null }) {
  const [draft, setDraft] = useState(query ?? '')
  const [topK, setTopK] = useState('5')
  const active = query ?? ''
  const search = useAsync(() => api.references(active, Number(topK)), `${active}:${topK}`)
  const result = search.data
  const stats = result?.stats

  function submit(text: string) {
    setDraft(text)
    navigate({ page: 'references', query: text.trim() || null })
  }

  return (
    <div className="mx-auto h-full max-w-4xl space-y-4 overflow-auto p-6">
      <div className="space-y-1">
        <h1 className="text-lg font-semibold">混合检索</h1>
        <p className="text-sm text-muted-foreground">
          planner 写契约前调用的 <code className="font-mono">search_references</code>。结果只有指针、标题和首句摘要，
          不含正文；planner 把用到的指针写进 stage 的 sources，写入时逐个校验是否是索引发出的。
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {stats && (
          <>
            <Badge variant="secondary">{stats.chunks} 个小节</Badge>
            <Badge
              variant="outline"
              className={cn(
                'font-mono',
                stats.mode === 'hybrid'
                  ? 'border-emerald-500/40 text-emerald-700 dark:text-emerald-300'
                  : 'border-amber-500/40 text-amber-700 dark:text-amber-300',
              )}
            >
              mode {stats.mode}
            </Badge>
            <span className="text-muted-foreground">{MODE_NOTE[stats.mode]}</span>
          </>
        )}
      </div>
      {stats?.vector_error && (
        <Alert>
          <TriangleAlertIcon />
          <AlertTitle>embeddings 不可用，已降级</AlertTitle>
          <AlertDescription className="font-mono text-xs break-all">{stats.vector_error}</AlertDescription>
        </Alert>
      )}

      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          submit(draft)
        }}
      >
        <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="例如：formal tone claims and evidence" />
        <Select value={topK} onValueChange={setTopK}>
          <SelectTrigger className="w-24">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[1, 2, 3, 5, 8].map((k) => (
              <SelectItem key={k} value={String(k)}>
                top {k}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="submit">
          {search.loading ? <Spinner /> : <SearchIcon />}
          检索
        </Button>
      </form>
      <div className="flex flex-wrap gap-1.5">
        {EXAMPLES.map((example) => (
          <Button key={example} size="xs" variant="outline" onClick={() => submit(example)}>
            {example}
          </Button>
        ))}
      </div>

      {search.error && <p className="text-sm text-destructive">{search.error}</p>}

      {active && result && result.hits.length === 0 && (
        <p className="text-sm text-muted-foreground">没有命中。BM25 只认共同的词；换个说法，或配置 embeddings 走向量那一路。</p>
      )}
      {!active && (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <BookOpenTextIcon />
            </EmptyMedia>
            <EmptyTitle>输入查询，或点一个示例</EmptyTitle>
            <EmptyDescription>
              最后一个示例是改写式查询：只有 BM25 时，它排不到 beginners 规范，这正是向量召回要补的。
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}

      <div className="space-y-2">
        {result?.hits.map((hit, index) => (
          <Card key={hit.pointer} size="sm">
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-mono text-muted-foreground">#{index + 1}</span>
                {hit.title}
              </CardTitle>
              <CardDescription className="flex flex-wrap items-center gap-1.5">
                <button
                  className="flex items-center gap-1 font-mono text-foreground hover:underline"
                  onClick={() => {
                    void navigator.clipboard?.writeText(hit.pointer)
                    toast.success(`已复制 ${hit.pointer}`)
                  }}
                >
                  {hit.pointer}
                  <CopyIcon className="size-3" />
                </button>
                <Badge variant="outline" className="font-mono">RRF {hit.score.toFixed(5)}</Badge>
                {hit.matched_by.map((ranker) => (
                  <Badge key={ranker} variant="secondary" className="font-mono">
                    {ranker}
                  </Badge>
                ))}
              </CardDescription>
            </CardHeader>
            <CardContent className="text-sm">{hit.summary}</CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
