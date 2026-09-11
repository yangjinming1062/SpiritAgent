/** 2D 木偶四肢节段划分与分档判定（Limb Split & Tier Assessment）
 *
 * 职责：
 * 1. 纯函数逻辑：评估 PSD 的四肢完整度档位（segmented / sided / blob）。
 * 2. 蒙皮切段位置由骨架绑定关节提供，各图层共用同一套关节基准。
 * 3. 虚拟切段权重分配：在同一张 ArtMesh 上按顶点 Y 坐标计算 upper / lower / end 骨骼权重。
 */

import type { LimbTier, Rig } from './puppet-types'

/** smooth [0, 1] 立方插值（与 mesh2d 共享）。 */
export function smooth(t: number): number {
  t = t < 0 ? 0 : t > 1 ? 1 : t

  return t * t * (3 - 2 * t)
}

/** 平滑阶跃插值 [0, 1] */
export function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = smooth((x - edge0) / (edge1 - edge0 || 1))

  return t
}

export interface LimbCutLines {
  joint0: number // 肩或髋 Y
  joint1: number // 肘或膝 Y
  joint2: number // 腕或踝 Y
}

/** 评估 PSD 资产的四肢分档 */
export function assessLimbTier(rig: Rig): LimbTier {
  const names = rig.layers.map(l => l.name.toLowerCase())

  // L3 完整节段：上游已直接给出 upper/lower/hand
  const hasArmSegments =
    names.some(n => /arm.*upper|arm_upper/.test(n)) && names.some(n => /arm.*lower|arm_lower/.test(n))

  const hasLegSegments =
    names.some(n => /leg.*upper|leg_upper/.test(n)) && names.some(n => /leg.*lower|leg_lower/.test(n))

  if (hasArmSegments || hasLegSegments) {
    return 'segmented'
  }

  // L2/L1 左右整肢：至少存在左右分侧的 handwear 或 legwear。检查 L/R 任一即可——
  // 见-through 输出 -l/-r 但早期 PSD 只有单侧 side 标签，单边也算 sided（半装配降级）。
  const hasSidedArms =
    (names.some(n => /handwear.*[_-]l|arm.*[_-]l/.test(n)) && names.some(n => /handwear.*[_-]r|arm.*[_-]r/.test(n))) ||
    rig.layers.some(
      l => (l.name.startsWith('handwear') || l.name.startsWith('arm')) && (l.side === 'L' || l.side === 'R')
    )

  const hasSidedLegs =
    (names.some(n => /legwear.*[_-]l|leg.*[_-]l/.test(n)) && names.some(n => /legwear.*[_-]r|leg.*[_-]r/.test(n))) ||
    rig.layers.some(
      l => (l.name.startsWith('legwear') || l.name.startsWith('leg')) && (l.side === 'L' || l.side === 'R')
    )

  if (hasSidedArms || hasSidedLegs) {
    return 'sided'
  }

  // L0 blob：仅有未分侧的整块 handwear/legwear
  return 'blob'
}

/**
 * 虚拟切段权重分配：按顶点 Y 坐标计算 3 段骨骼（upper / lower / end）的权重分布
 * @param y 顶点世界 Y 坐标
 * @param cuts 关节分割线
 * @param rootBlendNearJoint0 是否在最上端保留一定根骨骼混合权重（例如肩/腋下防撕裂）
 */
export function sampleLimbWeights(
  y: number,
  cuts: LimbCutLines,
  rootBlendNearJoint0 = false
): { rootWeight: number; upperWeight: number; lowerWeight: number; endWeight: number } {
  const { joint0, joint1, joint2 } = cuts

  // 过渡带半宽
  const span01 = Math.max(10, (joint1 - joint0) * 0.35)
  const span12 = Math.max(10, (joint2 - joint1) * 0.25)

  // 1. 上段与根部（腋下/髋部）混合
  let rootWeight = 0

  if (rootBlendNearJoint0 && y < joint0 + span01) {
    const t = smoothstep(joint0 + span01, joint0 - span01, y)
    rootWeight = t * 0.15 // 最多 15% 归属脊柱/根部
  }

  // 2. upper vs lower
  let upperVsLower = 0 // 0 = upper, 1 = lower

  if (y <= joint1 - span01) {
    upperVsLower = 0
  } else if (y >= joint1 + span01) {
    upperVsLower = 1
  } else {
    upperVsLower = smoothstep(joint1 - span01, joint1 + span01, y)
  }

  // 3. lower vs end (wrist/hand or ankle/foot)
  let lowerVsEnd = 0

  if (y <= joint2 - span12) {
    lowerVsEnd = 0
  } else if (y >= joint2 + span12) {
    lowerVsEnd = 1
  } else {
    lowerVsEnd = smoothstep(joint2 - span12, joint2 + span12, y)
  }

  const upperWeight = (1 - rootWeight) * (1 - upperVsLower)
  const lowerWeight = (1 - rootWeight) * upperVsLower * (1 - lowerVsEnd)
  const endWeight = (1 - rootWeight) * upperVsLower * lowerVsEnd

  return { rootWeight, upperWeight, lowerWeight, endWeight }
}
