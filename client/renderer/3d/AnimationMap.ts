// 动画来源唯一：供应商烘焙进 GLB 的 clip。后端下发「语义键 → 我们向供应商提交的 clip 名」映射，
// 客户端只做兑现，不持有任何供应商命名。
//
// 供应商未承诺写进 GLB 的 clip 名与提交的 token 逐字相同（可能带 Armature| 前缀、去掉命名空间或改变大小写），
// 故分级兑现而非全等查找。

export type ClipMap = Readonly<Record<string, string>>

/** 语义键缺席时的通用回退：idle 是产品级语义键（非供应商命名），任何状态都可以退到它。 */
const FALLBACK_KEY = 'idle'

function leafOf(token: string): string {
  const parts = token.split(':')

  return (parts[parts.length - 1] ?? token).toLowerCase()
}

function matchAvailable(candidate: string, available: ReadonlySet<string>): string | null {
  if (available.has(candidate)) {
    return candidate
  }

  const leaf = leafOf(candidate)
  let partial: string | null = null

  for (const name of available) {
    const lower = name.toLowerCase()

    if (lower === leaf) {
      return name
    }

    if (partial === null && lower.includes(leaf)) {
      partial = name
    }
  }

  return partial
}

/** 把语义键兑现为 GLB 里真实存在的 clip 名；映射缺键或三级兑现全落空时返回 null（角色停在绑定姿势）。 */
export function resolveClip(key: string, clipMap: ClipMap, available: ReadonlySet<string>): string | null {
  const candidate = clipMap[key] ?? clipMap[FALLBACK_KEY]

  return candidate ? matchAvailable(candidate, available) : null
}
