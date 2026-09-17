import { CheckIcon } from 'lucide-react'
import { useState } from 'react'

import { JsonBlock } from '@/components/json-block'
import { RoleBadge } from '@/components/status-badges'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAsync } from '@/hooks/use-async'
import { api } from '@/lib/api'
import { typeLabel } from '@/lib/schema'
import { ROLES, type ToolInfo } from '@/lib/types'
import { cn } from '@/lib/utils'

export function ToolsPage() {
  const catalog = useAsync(() => api.tools(), 'tools')
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const tools = catalog.data?.tools ?? []
  const selected = tools.find((tool) => tool.name === selectedName) ?? tools[0]

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_minmax(420px,45%)]">
      <div className="min-h-0 space-y-3 overflow-auto p-6">
        <div className="space-y-1">
          <h1 className="text-lg font-semibold">工具注册表</h1>
          <p className="text-sm text-muted-foreground">
            每个工具的参数模型由函数签名生成，一份定义两处用：导出成 JSON schema 给模型看，调用前用同一个模型校验。
            哪个角色能用哪个工具只在 <code className="font-mono">agents/roles.py</code> 一张表里。注入的调用方角色（RunContext）不出现在任何 schema 里。
          </p>
        </div>
        {catalog.error && <p className="text-sm text-destructive">{catalog.error}</p>}
        {catalog.loading && !tools.length && <Skeleton className="h-96 w-full" />}
        {tools.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>工具</TableHead>
                {ROLES.map((role) => (
                  <TableHead key={role} className="text-center">
                    <RoleBadge role={role} />
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {tools.map((tool) => (
                <TableRow
                  key={tool.name}
                  onClick={() => setSelectedName(tool.name)}
                  data-state={tool.name === selected?.name ? 'selected' : undefined}
                  className="cursor-pointer"
                >
                  <TableCell className="font-mono text-xs">{tool.name}</TableCell>
                  {ROLES.map((role) => (
                    <TableCell key={role} className="text-center">
                      {tool.roles.includes(role) && <CheckIcon className="mx-auto size-4 text-emerald-600" />}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      <aside className="min-h-0 overflow-auto border-l p-6">{selected && <ToolDetail tool={selected} />}</aside>
    </div>
  )
}

function ToolDetail({ tool }: { tool: ToolInfo }) {
  const [raw, setRaw] = useState(false)
  const schema = tool.parameters
  const required = new Set(schema.required ?? [])
  const properties = Object.entries(schema.properties ?? {})
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <h2 className="font-mono text-base font-semibold">{tool.name}</h2>
        <p className="text-sm">{tool.description}</p>
        <div className="flex flex-wrap gap-1.5">
          {tool.roles.length ? (
            tool.roles.map((role) => <RoleBadge key={role} role={role} />)
          ) : (
            <Badge variant="outline">没有角色持有</Badge>
          )}
          {schema.additionalProperties === false && <Badge variant="secondary">additionalProperties: false</Badge>}
        </div>
      </div>

      <label className="flex items-center gap-2 text-xs">
        <Switch size="sm" checked={raw} onCheckedChange={setRaw} />
        看原始 JSON schema（模型实际看到的）
      </label>

      {raw ? (
        <JsonBlock value={schema} className="max-h-none" />
      ) : properties.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>参数</TableHead>
              <TableHead>类型</TableHead>
              <TableHead>说明</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {properties.map(([name, property]) => (
              <TableRow key={name} className="align-top">
                <TableCell className="font-mono text-xs">
                  {name}
                  {required.has(name) && <span className="text-destructive">*</span>}
                </TableCell>
                <TableCell className="max-w-48 font-mono text-xs break-words whitespace-normal">
                  {typeLabel(property, schema.$defs ?? {})}
                  {property.default !== undefined && (
                    <div className="text-muted-foreground">default {JSON.stringify(property.default)}</div>
                  )}
                </TableCell>
                <TableCell className={cn('text-xs whitespace-normal', !property.description && 'text-muted-foreground')}>
                  {property.description ?? '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-sm text-muted-foreground">没有参数。</p>
      )}
    </div>
  )
}
