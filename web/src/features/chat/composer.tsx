import { ArchiveIcon, PaperclipIcon, PlusIcon, RepeatIcon, SendIcon, TriangleAlertIcon, XIcon } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { api, describeError } from '@/lib/api'
import { newClientMessageId } from '@/lib/format'
import type { NewAsset, SendMessageBody } from '@/lib/types'
import { cn } from '@/lib/utils'

const EXAMPLES: { label: string; text: string }[] = [
  { label: '直接生成', text: 'Write one short playful article about https://example.com/p/1 as a PDF.' },
  {
    label: '工作流 + 评审',
    text: 'Write a two-part series about https://example.com/p/1 for developers, playful tone, PDF. Show me the plan first.',
  },
  { label: '缺信息应提问', text: 'Write a series about our new product.' },
  { label: '批准', text: 'Approved. Please continue.' },
  { label: '跳过当前 stage', text: 'skip' },
  { label: '聊天里归档', text: 'Please archive logo, we no longer use it.' },
]

const ASSET_PRESETS: NewAsset[] = [
  { name: 'hero', kind: 'image', source_id: 'upload:hero-v1', summary: 'Product hero shot, red background' },
  { name: 'logo', kind: 'image', source_id: 'upload:logo', summary: 'Square logo' },
  { name: 'hero', kind: 'image', source_id: 'upload:hero-v1', summary: 'Same source again: updates, no duplicate' },
  { name: 'hero', kind: 'image', source_id: 'upload:hero-v2', summary: 'New source, same name: gets a numbered name' },
]

interface Outcome {
  tone: 'ok' | 'warn' | 'error'
  text: string
}

export function Composer({
  sessionId,
  running,
  archive,
  onArchiveChange,
  onSent,
  text,
  onTextChange: setText,
}: {
  sessionId: string
  running: boolean
  archive: string[]
  onArchiveChange: (names: string[]) => void
  onSent: () => void
  text: string
  onTextChange: (text: string) => void
}) {
  const [clientId, setClientId] = useState(newClientMessageId)
  const [attachments, setAttachments] = useState<NewAsset[]>([])
  const [sending, setSending] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [lastBody, setLastBody] = useState<SendMessageBody | null>(null)
  const [archiveDraft, setArchiveDraft] = useState('')

  async function send(body: SendMessageBody, resend: boolean) {
    setSending(true)
    try {
      const started = await api.sendMessage(sessionId, body)
      setLastBody(body)
      if (started.duplicate) {
        setOutcome({
          tone: 'warn',
          text: `202 duplicate：client_message_id ${body.client_message_id} 已存在，返回原来的 ${started.turn_id}，没有启动新的 turn`,
        })
      } else {
        setOutcome({ tone: 'ok', text: `202 started：${started.turn_id} · ${started.message_id}` })
      }
      if (!resend) {
        setText('')
        setAttachments([])
        onArchiveChange([])
        setClientId(newClientMessageId())
      }
      onSent()
    } catch (err) {
      setOutcome({ tone: 'error', text: describeError(err) })
    } finally {
      setSending(false)
    }
  }

  function submit() {
    const content = text.trim()
    if (!content || sending) return
    void send(
      {
        content,
        client_message_id: clientId.trim() || undefined,
        attachments: attachments.length ? attachments : undefined,
        archive_assets: archive.length ? archive : undefined,
      },
      false,
    )
  }

  return (
    <div className="border-t bg-background px-4 py-3">
      <div className="mb-2 flex flex-wrap gap-1.5">
        {EXAMPLES.map((example) => (
          <Button key={example.label} variant="outline" size="xs" onClick={() => setText(example.text)}>
            {example.label}
          </Button>
        ))}
      </div>

      {(attachments.length > 0 || archive.length > 0) && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {attachments.map((asset, index) => (
            <Badge key={`a-${index}`} variant="secondary" className="gap-1 font-mono">
              <PaperclipIcon />
              {asset.name} · {asset.source_id}
              <button aria-label="移除附件" onClick={() => setAttachments(attachments.filter((_, i) => i !== index))}>
                <XIcon className="size-3" />
              </button>
            </Badge>
          ))}
          {archive.map((name) => (
            <Badge key={`r-${name}`} variant="outline" className="gap-1 border-amber-500/40 font-mono text-amber-700 dark:text-amber-300">
              <ArchiveIcon />
              归档 {name}
              <button aria-label="取消归档" onClick={() => onArchiveChange(archive.filter((n) => n !== name))}>
                <XIcon className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}

      <Textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            submit()
          }
        }}
        placeholder="发消息给 orchestrator（Enter 发送，Shift+Enter 换行）"
        className="min-h-20 resize-none"
      />

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <AttachmentPopover onAdd={(asset) => setAttachments([...attachments, asset])} />
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm">
              <ArchiveIcon />
              归档名
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-72 space-y-2">
            <p className="text-xs text-muted-foreground">
              随下一条消息从面板归档。也可以填一个不存在的名字，测整条消息被 422 拒绝、什么都不落库。
            </p>
            <form
              className="flex gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                const name = archiveDraft.trim()
                if (name && !archive.includes(name)) onArchiveChange([...archive, name])
                setArchiveDraft('')
              }}
            >
              <Input value={archiveDraft} onChange={(e) => setArchiveDraft(e.target.value)} placeholder="资产名或 id" />
              <Button type="submit" size="sm">加入</Button>
            </form>
          </PopoverContent>
        </Popover>

        <div className="ml-auto flex items-center gap-1.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="text-[11px] text-muted-foreground">client_message_id</span>
            </TooltipTrigger>
            <TooltipContent>会话内唯一。重发同一个 id 不会再跑一遍，返回原来的 turn。</TooltipContent>
          </Tooltip>
          <Input value={clientId} onChange={(e) => setClientId(e.target.value)} className="h-7 w-32 font-mono text-xs" />
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                disabled={!lastBody || sending}
                onClick={() => lastBody && void send(lastBody, true)}
              >
                <RepeatIcon />
                原样重发
              </Button>
            </TooltipTrigger>
            <TooltipContent>把上一次的请求体（含 client_message_id）原样再发一次</TooltipContent>
          </Tooltip>
          <Button size="sm" onClick={submit} disabled={sending || !text.trim()}>
            <SendIcon />
            发送
          </Button>
        </div>
      </div>

      <div className="mt-2 flex min-h-5 flex-wrap items-center gap-2 text-xs">
        {running && (
          <span className="flex items-center gap-1 text-amber-700 dark:text-amber-300">
            <TriangleAlertIcon className="size-3.5" />
            有 turn 在运行：此时发一条新消息会得到 409（租约被占用）
          </span>
        )}
        {outcome && (
          <span
            className={cn(
              'font-mono break-all',
              outcome.tone === 'ok' && 'text-emerald-700 dark:text-emerald-300',
              outcome.tone === 'warn' && 'text-amber-700 dark:text-amber-300',
              outcome.tone === 'error' && 'text-destructive',
            )}
          >
            {outcome.text}
          </span>
        )}
      </div>
    </div>
  )
}

function AttachmentPopover({ onAdd }: { onAdd: (asset: NewAsset) => void }) {
  const [draft, setDraft] = useState<NewAsset>({ name: '', kind: 'image', source_id: '', summary: '' })
  const valid = draft.name.trim() && draft.kind.trim() && draft.source_id.trim()
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          <PaperclipIcon />
          附件
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-96 space-y-3">
        <p className="text-xs text-muted-foreground">
          附件随消息在同一个事务里登记。资产以 source_id 为身份：同一来源再登记只更新，不新增；同名不同源会自动编号。
        </p>
        <div className="flex flex-wrap gap-1.5">
          {ASSET_PRESETS.map((preset, index) => (
            <Button key={index} variant="secondary" size="xs" onClick={() => onAdd(preset)}>
              <PlusIcon />
              {preset.name} · {preset.source_id}
            </Button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Input placeholder="name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <Input placeholder="kind" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })} />
          <Input
            placeholder="source_id"
            className="col-span-2 font-mono"
            value={draft.source_id}
            onChange={(e) => setDraft({ ...draft, source_id: e.target.value })}
          />
          <Input
            placeholder="summary"
            className="col-span-2"
            value={draft.summary}
            onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
          />
        </div>
        <Button
          size="sm"
          className="w-full"
          disabled={!valid}
          onClick={() => {
            onAdd({ ...draft, name: draft.name.trim(), source_id: draft.source_id.trim() })
            setDraft({ name: '', kind: 'image', source_id: '', summary: '' })
          }}
        >
          加入附件
        </Button>
      </PopoverContent>
    </Popover>
  )
}
