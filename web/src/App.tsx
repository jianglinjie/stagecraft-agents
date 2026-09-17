import { MoonIcon, SunIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { ChatPage } from '@/features/chat/chat-page'
import { EvalsPage } from '@/features/evals/evals-page'
import { ReferencesPage } from '@/features/references/references-page'
import { ToolsPage } from '@/features/tools/tools-page'
import { useAsync } from '@/hooks/use-async'
import { api } from '@/lib/api'
import { hrefFor, useRoute, type Route } from '@/lib/route'
import { cn } from '@/lib/utils'

const NAV: { page: Route['page']; label: string; hint: string; route: Route }[] = [
  { page: 'chat', label: '会话', hint: '里程碑 2–4', route: { page: 'chat', sessionId: null } },
  { page: 'references', label: '检索', hint: '里程碑 7', route: { page: 'references', query: null } },
  { page: 'tools', label: '工具', hint: '里程碑 1', route: { page: 'tools' } },
  { page: 'evals', label: '评测', hint: '里程碑 6', route: { page: 'evals' } },
]

function useTheme() {
  const [dark, setDark] = useState(() => {
    try {
      const saved = localStorage.getItem('stagecraft-theme')
      return saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches
    } catch {
      return false
    }
  })
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    try {
      localStorage.setItem('stagecraft-theme', dark ? 'dark' : 'light')
    } catch {
      // storage unavailable: the choice lasts for this page only
    }
  }, [dark])
  return [dark, setDark] as const
}

export default function App() {
  const route = useRoute()
  const [dark, setDark] = useTheme()
  const [tick, setTick] = useState(0)
  const info = useAsync(() => api.info(), `info:${tick}`)

  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 15000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <TooltipProvider>
      <div className="flex h-screen flex-col">
        <header className="flex items-center gap-4 border-b px-4 py-2">
          <span className="text-sm font-semibold">Stagecraft 控制台</span>
          <nav className="flex gap-1">
            {NAV.map((item) => (
              <a
                key={item.page}
                href={hrefFor(item.route)}
                title={item.hint}
                className={cn(
                  'rounded-md px-2.5 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground',
                  route.page === item.page && 'bg-muted text-foreground',
                )}
              >
                {item.label}
              </a>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2 text-xs">
            {info.error ? (
              <Badge variant="destructive">API 不可用：{info.error}</Badge>
            ) : info.data ? (
              <>
                <Badge variant="outline" className="gap-1.5">
                  <span className="size-1.5 rounded-full bg-emerald-500" />
                  模型 <span className="font-mono">{info.data.model}</span>
                </Badge>
                <Badge variant="outline" className="font-mono">
                  检索 {info.data.references.mode}
                </Badge>
              </>
            ) : null}
            <Button size="icon-sm" variant="ghost" onClick={() => setDark(!dark)} aria-label="切换主题">
              {dark ? <SunIcon /> : <MoonIcon />}
            </Button>
          </div>
        </header>
        <main className="min-h-0 flex-1">
          {route.page === 'chat' && <ChatPage sessionId={route.sessionId} />}
          {route.page === 'references' && <ReferencesPage key={route.query ?? ''} query={route.query} />}
          {route.page === 'tools' && <ToolsPage />}
          {route.page === 'evals' && <EvalsPage />}
        </main>
      </div>
      <Toaster theme={dark ? 'dark' : 'light'} />
    </TooltipProvider>
  )
}
