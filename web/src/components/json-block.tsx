import { cn } from '@/lib/utils'
import { pretty } from '@/lib/format'

export function JsonBlock({ value, className }: { value: unknown; className?: string }) {
  return (
    <pre
      className={cn(
        'max-h-72 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-[11px] leading-relaxed break-all whitespace-pre-wrap',
        className,
      )}
    >
      {pretty(value)}
    </pre>
  )
}
