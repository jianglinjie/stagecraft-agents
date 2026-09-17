import type { JsonSchema } from '@/lib/types'

/** A JSON schema property as a short type: refs resolved to enum values, unions joined. */
export function typeLabel(schema: JsonSchema, defs: Record<string, JsonSchema>): string {
  if (schema.$ref) {
    const name = schema.$ref.split('/').at(-1) ?? schema.$ref
    const target = defs[name]
    return target?.enum ? target.enum.map((v) => JSON.stringify(v)).join(' | ') : name
  }
  if (schema.enum) return schema.enum.map((v) => JSON.stringify(v)).join(' | ')
  if (schema.anyOf) return schema.anyOf.map((option) => typeLabel(option, defs)).join(' | ')
  const bounds = [
    schema.minimum !== undefined ? `≥${schema.minimum}` : '',
    schema.maximum !== undefined ? `≤${schema.maximum}` : '',
  ]
    .filter(Boolean)
    .join(' ')
  if (schema.type === 'array') return `${schema.items ? typeLabel(schema.items, defs) : 'any'}[]`
  const type = Array.isArray(schema.type) ? schema.type.join(' | ') : (schema.type ?? 'any')
  return bounds ? `${type} (${bounds})` : type
}
