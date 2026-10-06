/** JSON 形态纯数据的结构化全等：不看键序，仅比较自有可枚举键与值。 */
export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) {
    return true
  }

  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) {
    return false
  }

  const left = a as Record<string, unknown>
  const right = b as Record<string, unknown>

  if (Array.isArray(left) !== Array.isArray(right)) {
    return false
  }

  const keys = Object.keys(left)

  return keys.length === Object.keys(right).length && keys.every(key => deepEqual(left[key], right[key]))
}
