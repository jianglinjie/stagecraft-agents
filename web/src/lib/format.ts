export function clockTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleTimeString('zh-CN', { hour12: false })
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString('zh-CN', { hour12: false })
}

export function pretty(value: unknown): string {
  if (value === undefined) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

export function clip(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > limit ? `${flat.slice(0, limit - 1)}…` : flat
}

/** The arguments worth showing inline for a call: a short key=value list. */
export function inlineArgs(args: unknown, limit = 90): string {
  if (args === null || typeof args !== 'object') return args === undefined ? '' : clip(String(args), limit)
  const parts = Object.entries(args as Record<string, unknown>)
    .filter(([, value]) => value !== null && value !== undefined && !(Array.isArray(value) && !value.length))
    .map(([key, value]) => `${key}=${typeof value === 'string' ? value : JSON.stringify(value)}`)
  return clip(parts.join(' '), limit)
}

export function newClientMessageId(): string {
  return `web-${crypto.randomUUID().slice(0, 8)}`
}

export function plural(count: number, word: string): string {
  return `${count} ${word}`
}
