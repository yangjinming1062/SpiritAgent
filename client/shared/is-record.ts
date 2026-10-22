/** 普通对象（非 null、非数组）守卫，用于校验外部输入。 */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
