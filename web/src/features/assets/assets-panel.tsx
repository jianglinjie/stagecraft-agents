import { ArchiveIcon, ImagesIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { dateTime } from '@/lib/format'
import type { Asset } from '@/lib/types'

export function AssetsPanel({
  assets,
  archived,
  selected,
  onSelectedChange,
}: {
  assets: Asset[]
  archived: Asset[]
  selected: string[]
  onSelectedChange: (names: string[]) => void
}) {
  if (!assets.length && !archived.length) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ImagesIcon />
          </EmptyMedia>
          <EmptyTitle>资产池是空的</EmptyTitle>
          <EmptyDescription>
            在输入框的“附件”里加 hero、logo 等资产，随下一条消息登记。
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  function toggle(name: string, on: boolean) {
    onSelectedChange(on ? [...selected, name] : selected.filter((n) => n !== name))
  }

  return (
    <div className="space-y-4 p-3 text-xs">
      <section className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          活跃资产 <Badge variant="secondary">{assets.length}</Badge>
        </h3>
        <p className="text-muted-foreground">勾选后随下一条消息从面板归档（原因记为 panel）。归档不可恢复。</p>
        <ul className="space-y-1.5">
          {assets.map((asset) => (
            <li key={asset.id} className="flex items-start gap-2 rounded-lg border p-2">
              <Checkbox
                className="mt-0.5"
                checked={selected.includes(asset.name)}
                onCheckedChange={(checked) => toggle(asset.name, checked === true)}
                aria-label={`归档 ${asset.name}`}
              />
              <div className="min-w-0 flex-1 space-y-0.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono font-medium">{asset.name}</span>
                  <Badge variant="outline">{asset.kind}</Badge>
                  <span className="font-mono text-muted-foreground">{asset.source_id}</span>
                </div>
                {asset.summary && <p>{asset.summary}</p>}
                <p className="text-muted-foreground">
                  登记于 {dateTime(asset.created_at)}
                  {asset.updated_at !== asset.created_at && ` · 同来源更新于 ${dateTime(asset.updated_at)}`}
                  {asset.registered_by_message && ` · 消息 ${asset.registered_by_message}`}
                </p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      {archived.length > 0 && (
        <section className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            已归档 <Badge variant="secondary">{archived.length}</Badge>
          </h3>
          <ul className="space-y-1.5">
            {archived.map((asset) => (
              <li key={asset.id} className="space-y-0.5 rounded-lg border border-dashed p-2 text-muted-foreground">
                <div className="flex flex-wrap items-center gap-1.5">
                  <ArchiveIcon className="size-3.5" />
                  <span className="font-mono line-through">{asset.name}</span>
                  <Badge variant="outline">{asset.archive?.reason}</Badge>
                  <span className="font-mono">{asset.source_id}</span>
                </div>
                <p>
                  {dateTime(asset.archive?.archived_at)}
                  {asset.archive?.message_id && ` · 由消息 ${asset.archive.message_id}`}
                  {asset.archive?.note && ` · ${asset.archive.note}`}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
