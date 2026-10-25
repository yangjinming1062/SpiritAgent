/** PostgreSQL BIGINT identities cross every JSON boundary as canonical decimal strings. */
const MAX_RESOURCE_ID = '9223372036854775807'

export function isResourceId(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    value[0] !== '0' &&
    !/[^0-9]/.test(value) &&
    (value.length < MAX_RESOURCE_ID.length || (value.length === MAX_RESOURCE_ID.length && value <= MAX_RESOURCE_ID))
  )
}

export function compareResourceIds(left: string, right: string): number {
  return left.length - right.length || (left < right ? -1 : left > right ? 1 : 0)
}
