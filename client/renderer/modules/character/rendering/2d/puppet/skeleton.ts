/** 2D 木偶骨骼系统（Skeleton & 2D Affine & 2-Bone Analytical IK）
 *
 * 核心数学：
 * 1. 2D 仿射变换矩阵：Float32Array(6) [a, b, c, d, tx, ty]，无 GC 高性能乘法与求逆。
 * 2. 中立骨架（Neutral Skeleton）：从 PSD 锚点与图层外包矩形估计自然站姿骨骼与绑定矩阵 M_bind。
 * 3. 2-Bone 解析 IK：基于余弦定理求肘/膝关节内角，集成解剖学关节角限位与超距伸展保护。
 * 4. 蒙皮矩阵：M_skin = M_world * M_bind^-1，直接作用于基准顶点 v' = M_skin * v。
 */

import { clamp } from '@runtime'

import type { Rig, RigPart } from './puppet-types'

// ---------- 2D 仿射矩阵运算 (Mat2D) ----------
export type Mat2D = Float32Array // length 6: [a, b, c, d, tx, ty]

export function createMat2D(): Mat2D {
  const m = new Float32Array(6)
  m[0] = 1
  m[3] = 1

  return m
}

export function copyMat2D(out: Mat2D, src: Mat2D): Mat2D {
  out[0] = src[0]!
  out[1] = src[1]!
  out[2] = src[2]!
  out[3] = src[3]!
  out[4] = src[4]!
  out[5] = src[5]!

  return out
}

export function identityMat2D(out: Mat2D): Mat2D {
  out[0] = 1
  out[1] = 0
  out[2] = 0
  out[3] = 1
  out[4] = 0
  out[5] = 0

  return out
}

export function multiplyMat2D(out: Mat2D, m1: Mat2D, m2: Mat2D): Mat2D {
  const a1 = m1[0]!
  const b1 = m1[1]!
  const c1 = m1[2]!
  const d1 = m1[3]!
  const tx1 = m1[4]!
  const ty1 = m1[5]!

  const a2 = m2[0]!
  const b2 = m2[1]!
  const c2 = m2[2]!
  const d2 = m2[3]!
  const tx2 = m2[4]!
  const ty2 = m2[5]!

  out[0] = a1 * a2 + c1 * b2
  out[1] = b1 * a2 + d1 * b2
  out[2] = a1 * c2 + c1 * d2
  out[3] = b1 * c2 + d1 * d2
  out[4] = a1 * tx2 + c1 * ty2 + tx1
  out[5] = b1 * tx2 + d1 * ty2 + ty1

  return out
}

export function invertMat2D(out: Mat2D, m: Mat2D): boolean {
  const a = m[0]!
  const b = m[1]!
  const c = m[2]!
  const d = m[3]!
  const tx = m[4]!
  const ty = m[5]!

  const det = a * d - b * c

  if (Math.abs(det) < 1e-12) {
    identityMat2D(out)

    return false
  }

  const invDet = 1.0 / det
  out[0] = d * invDet
  out[1] = -b * invDet
  out[2] = -c * invDet
  out[3] = a * invDet
  out[4] = (c * ty - d * tx) * invDet
  out[5] = (b * tx - a * ty) * invDet

  return true
}

export function transformPoint(m: Mat2D, x: number, y: number, out: { x: number; y: number }): void {
  out.x = m[0]! * x + m[2]! * y + m[4]!
  out.y = m[1]! * x + m[3]! * y + m[5]!
}

export function makeTRS(out: Mat2D, tx: number, ty: number, rad: number, sx = 1, sy = 1): Mat2D {
  const c = Math.cos(rad)
  const s = Math.sin(rad)
  out[0] = c * sx
  out[1] = s * sx
  out[2] = -s * sy
  out[3] = c * sy
  out[4] = tx
  out[5] = ty

  return out
}

// ---------- 骨骼枚举与层级 ----------
export type BoneId =
  | 'hip'
  | 'spine'
  | 'neck'
  | 'head'
  | 'shoulderL'
  | 'upperArmL'
  | 'lowerArmL'
  | 'handL'
  | 'shoulderR'
  | 'upperArmR'
  | 'lowerArmR'
  | 'handR'
  | 'upperLegL'
  | 'lowerLegL'
  | 'footL'
  | 'upperLegR'
  | 'lowerLegR'
  | 'footR'

export interface Bone {
  id: BoneId
  index: number
  parent: number | null // 父骨骼在骨骼序列中的索引，hip 为 null
  // 绑定姿态数据
  bindWorldPos: { x: number; y: number }
  bindWorldAngle: number
  length: number
  localPos: { x: number; y: number } // 绑定姿态下相对父骨骼的位置
  bindWorldMatrix: Mat2D
  invBindWorldMatrix: Mat2D
  // 运行时实时姿态（相对绑定的增量）
  localAngle: number // 局部相对旋转弧度
  localOffset: { x: number; y: number } // 局部相对平移
  // 变换结果
  worldMatrix: Mat2D
  skinMatrix: Mat2D // worldMatrix * invBindWorldMatrix
}

export interface SkeletonLimits {
  shoulder: { min: number; max: number }
  elbow: { min: number; max: number }
  hip: { min: number; max: number }
  knee: { min: number; max: number }
  ankle: { min: number; max: number }
}

export const ANATOMICAL_LIMITS: SkeletonLimits = {
  // 弧度制
  shoulder: { min: (-20 * Math.PI) / 180, max: (75 * Math.PI) / 180 },
  elbow: { min: 0, max: (110 * Math.PI) / 180 },
  hip: { min: (-15 * Math.PI) / 180, max: (40 * Math.PI) / 180 },
  knee: { min: 0, max: (70 * Math.PI) / 180 },
  ankle: { min: (-18 * Math.PI) / 180, max: (18 * Math.PI) / 180 }
}

// ---------- 2-Bone 解析 IK ----------
export interface IKResult {
  angle0: number // 第一段骨骼旋转角（弧度）
  angle1: number // 第二段骨骼相对第一段骨骼弯折角（弧度）
  reached: boolean
  stretch: number
}

/**
 * 2-Bone 解析 IK（余弦定理求解）
 * @param p0 第一段骨骼起点世界坐标
 * @param p1 第二段骨骼起点世界坐标
 * @param p2 末端执行器世界坐标
 * @param target 目标世界坐标
 * @param bendPositive 弯折方向（正/负，例如手臂折向腰部还是外侧）
 * @param limit0 第一段骨骼角度限位（相对中立方向）
 * @param limit1 第二段骨骼相对第一段角度限位
 */
export function solve2BoneIK(
  p0: { x: number; y: number },
  p1: { x: number; y: number },
  p2: { x: number; y: number },
  target: { x: number; y: number },
  bendPositive: boolean,
  limit0 = ANATOMICAL_LIMITS.shoulder,
  limit1 = ANATOMICAL_LIMITS.elbow
): IKResult {
  const v01x = p1.x - p0.x
  const v01y = p1.y - p0.y
  const l1 = Math.hypot(v01x, v01y) || 1
  const restTheta1 = Math.atan2(v01y, v01x)

  const v12x = p2.x - p1.x
  const v12y = p2.y - p1.y
  const l2 = Math.hypot(v12x, v12y) || 1
  const restTheta2 = Math.atan2(v12y, v12x)
  const restDeflection = restTheta2 - restTheta1

  const tx = target.x - p0.x
  const ty = target.y - p0.y
  const dist = Math.hypot(tx, ty)
  const targetAngle = Math.atan2(ty, tx)
  const stretch = Math.max(1.0, dist / (l1 + l2))

  let alpha = 0
  let beta = Math.PI

  if (dist < l1 + l2 - 1e-4) {
    const minReach = Math.max(0.01, Math.abs(l1 - l2) + 0.001)
    const clampedDist = clamp(dist, minReach, l1 + l2)
    const cosAlpha = clamp((l1 * l1 + clampedDist * clampedDist - l2 * l2) / (2 * l1 * clampedDist), -1, 1)
    const cosBeta = clamp((l1 * l1 + l2 * l2 - clampedDist * clampedDist) / (2 * l1 * l2), -1, 1)
    alpha = Math.acos(cosAlpha)
    beta = Math.acos(cosBeta)
  }

  let theta1: number
  let theta2: number

  if (bendPositive) {
    theta1 = targetAngle + alpha
    theta2 = theta1 - (Math.PI - beta)
  } else {
    theta1 = targetAngle - alpha
    theta2 = theta1 + (Math.PI - beta)
  }

  let delta0 = theta1 - restTheta1
  let delta1 = theta2 - theta1 - restDeflection

  // 规范化到 [-PI, PI]
  delta0 = Math.atan2(Math.sin(delta0), Math.cos(delta0))
  delta1 = Math.atan2(Math.sin(delta1), Math.cos(delta1))

  // 限位约束
  const clamped0 = clamp(delta0, limit0.min, limit0.max)
  const clamped1 = clamp(delta1, limit1.min, limit1.max)

  return {
    angle0: clamped0,
    angle1: clamped1,
    reached: stretch <= 1.05 && Math.abs(clamped0 - delta0) < 0.05 && Math.abs(clamped1 - delta1) < 0.05,
    stretch
  }
}

// ---------- 骨架构建与更新 ----------
export type LimbKind = 'arm' | 'leg'

interface LimbChain {
  upper: BoneId
  lower: BoneId
  end: BoneId
}

const LIMB_CHAINS: Record<LimbKind, Record<'l' | 'r', LimbChain>> = {
  arm: {
    l: { upper: 'upperArmL', lower: 'lowerArmL', end: 'handL' },
    r: { upper: 'upperArmR', lower: 'lowerArmR', end: 'handR' }
  },
  leg: {
    l: { upper: 'upperLegL', lower: 'lowerLegL', end: 'footL' },
    r: { upper: 'upperLegR', lower: 'lowerLegR', end: 'footR' }
  }
}

const LIMB_LIMITS: Record<LimbKind, { upper: { min: number; max: number }; lower: { min: number; max: number } }> = {
  arm: { upper: ANATOMICAL_LIMITS.shoulder, lower: ANATOMICAL_LIMITS.elbow },
  leg: { upper: ANATOMICAL_LIMITS.hip, lower: ANATOMICAL_LIMITS.knee }
}

export class Skeleton {
  bones: Bone[] = []
  boneMap = new Map<BoneId, Bone>()
  /** 复用缓冲：updateMatrices 每帧每骨骼一次 TRS 写入，避免每帧 18×Float32Array 分配。 */
  private readonly tempTRS = createMat2D()
  /** solve2BoneIK 输入点的复用缓冲：每帧 IK 调用 3 个点的固定分配。 */
  private readonly ikP0 = { x: 0, y: 0 }
  private readonly ikP1 = { x: 0, y: 0 }
  private readonly ikP2 = { x: 0, y: 0 }

  constructor(bones: Bone[]) {
    this.bones = bones

    for (const b of bones) {
      this.boneMap.set(b.id, b)
    }
  }

  getBone(id: BoneId): Bone | undefined {
    return this.boneMap.get(id)
  }

  /** 重置所有骨骼局部姿态至绑定状态 */
  resetPose(): void {
    for (const b of this.bones) {
      b.localAngle = 0
      b.localOffset.x = 0
      b.localOffset.y = 0
    }

    this.updateMatrices()
  }

  /** 按父子层级顺序计算所有骨骼的当前世界矩阵与蒙皮矩阵 */
  updateMatrices(): void {
    const tempTRS = this.tempTRS

    for (let i = 0; i < this.bones.length; i++) {
      const b = this.bones[i]!
      makeTRS(tempTRS, b.localPos.x + b.localOffset.x, b.localPos.y + b.localOffset.y, b.localAngle)

      if (b.parent === null) {
        copyMat2D(b.worldMatrix, tempTRS)
      } else {
        const parentBone = this.bones[b.parent]!
        multiplyMat2D(b.worldMatrix, parentBone.worldMatrix, tempTRS)
      }

      // M_skin = M_world * M_bind^-1
      multiplyMat2D(b.skinMatrix, b.worldMatrix, b.invBindWorldMatrix)
    }
  }

  /**
   * 应用 2-bone IK 目标到臂或腿。bendPositive 决定弯折方向（臂：!isL；腿：isL）。
   * 超距目标由解析求解连续夹到可达半径，骨长保持不变。
   */
  applyLimbIK(kind: LimbKind, side: 'l' | 'r', targetWorld: { x: number; y: number } | null): void {
    const chain = LIMB_CHAINS[kind][side]
    const limits = LIMB_LIMITS[kind]
    const isL = side === 'l'
    const bendPositive = kind === 'arm' ? !isL : isL

    const upper = this.getBone(chain.upper)
    const lower = this.getBone(chain.lower)
    const end = this.getBone(chain.end)

    if (!upper || !lower || !end) {
      return
    }

    if (!targetWorld) {
      upper.localAngle = 0
      lower.localAngle = 0
      end.localAngle = 0

      return
    }

    const p0 = this.ikP0
    const p1 = this.ikP1
    const p2 = this.ikP2
    p0.x = upper.worldMatrix[4]!
    p0.y = upper.worldMatrix[5]!
    p1.x = lower.worldMatrix[4]!
    p1.y = lower.worldMatrix[5]!
    p2.x = end.worldMatrix[4]!
    p2.y = end.worldMatrix[5]!
    // 镜像肢体的角限位也必须镜像；右肘弯折为负角，不能被正角限位截为零。
    const upperLimit = isL ? limits.upper : { min: -limits.upper.max, max: -limits.upper.min }
    const lowerLimit = bendPositive ? { min: -limits.lower.max, max: -limits.lower.min } : limits.lower
    const res = solve2BoneIK(p0, p1, p2, targetWorld, bendPositive, upperLimit, lowerLimit)

    upper.localAngle = res.angle0
    lower.localAngle = res.angle1

    if (kind === 'arm') {
      // 腕部 30% 随肘，自然收口
      end.localAngle = res.angle1 * 0.3
    } else {
      // 脚板微调补偿，钉地感
      end.localAngle = clamp(-res.angle0 - res.angle1, ANATOMICAL_LIMITS.ankle.min, ANATOMICAL_LIMITS.ankle.max)
    }
  }
}

/** 从 RigAnchors 与图层几何估算中立骨架 */
export function buildSkeleton(rig: Rig): Skeleton {
  const A = rig.anchors
  const H = rig.canvas.h
  const faceW = A.face.x1 - A.face.x0
  const faceH = A.face.y1 - A.face.y0
  const neckY = A.neckPivot?.cy ?? A.neckBottom ?? A.face.y1 + faceH * 0.1
  const neckX = A.neckPivot?.cx ?? A.face.cx
  const neckBottom = A.neckBottom ?? neckY
  const bodyH = Math.max(100, H - neckY)

  // 骨架 L/R 表示屏幕左右；PSD 后缀可能表示角色自身左右，不能据此跨肩绑骨。
  const findLimb = (
    kind: 'arm' | 'leg',
    left: boolean
  ): { x: number; y: number; w: number; h: number; layers: RigPart[] } | null => {
    const layers = rig.layers.filter(
      l =>
        (kind === 'arm' ? /^(handwear|arm|hand)(?:[_ -]|$)/i : /^(legwear|leg|legs)(?:[_ -]|$)/i).test(l.name) &&
        l.x + l.w / 2 < neckX === left
    )

    if (!layers.length) {
      return null
    }

    const x = Math.min(...layers.map(l => l.x))
    const y = Math.min(...layers.map(l => l.y))

    return {
      x,
      y,
      w: Math.max(...layers.map(l => l.x + l.w)) - x,
      h: Math.max(...layers.map(l => l.y + l.h)) - y,
      layers
    }
  }

  const handL = findLimb('arm', true)
  const handR = findLimb('arm', false)
  const legL = findLimb('leg', true)
  const legR = findLimb('leg', false)

  const limbX = (limb: NonNullable<ReturnType<typeof findLimb>>, y: number): number => {
    let weightedX = 0
    let weight = 0

    for (const layer of limb.layers) {
      const row = Math.round(y - layer.y)

      if (row < 0 || row >= layer.img.height) {
        continue
      }

      for (let x = 0; x < layer.img.width; x++) {
        const alpha = layer.img.data[(row * layer.img.width + x) * 4 + 3]!
        weightedX += (layer.x + x) * alpha
        weight += alpha
      }
    }

    return weight ? weightedX / weight : limb.x + limb.w / 2
  }

  // 1. 估算关节位置
  const legRoots = [legL, legR].filter(l => l !== null)

  const hipPos = {
    x: A.hip?.x ?? neckX,
    y: legRoots.length
      ? legRoots.reduce((sum, l) => sum + l.y, 0) / legRoots.length
      : (A.hip?.y ?? neckY + bodyH * 0.45)
  }

  const spinePos = { x: (hipPos.x + neckX) * 0.5, y: (hipPos.y + neckY) * 0.5 }
  const neckPos = { x: neckX, y: neckY }
  const headPos = { x: A.face.cx, y: A.face.cy }

  const shYL = A.shoulderL?.y ?? neckBottom + faceH * 0.15
  const shYR = A.shoulderR?.y ?? neckBottom + faceH * 0.15
  const shXL = A.shoulderL?.x ?? A.face.cx - faceW * 0.55
  const shXR = A.shoulderR?.x ?? A.face.cx + faceW * 0.55

  // 臂长参考
  const armLen = bodyH * 0.46

  const wristYL = handL ? handL.y + handL.h * 0.88 : shYL + armLen
  const wristYR = handR ? handR.y + handR.h * 0.88 : shYR + armLen
  const wristXL = handL ? limbX(handL, wristYL) : shXL - 15
  const wristXR = handR ? limbX(handR, wristYR) : shXR + 15

  const elbowYL = (shYL + wristYL) * 0.5
  const elbowYR = (shYR + wristYR) * 0.5
  const elbowXL = (shXL + wristXL) * 0.5
  const elbowXR = (shXR + wristXR) * 0.5

  // 腿部关节
  const hipHalfW = Math.max(20, faceW * 0.42)
  const hipLPos = { x: A.hipL?.x ?? hipPos.x - hipHalfW, y: legL?.y ?? hipPos.y }
  const hipRPos = { x: A.hipR?.x ?? hipPos.x + hipHalfW, y: legR?.y ?? hipPos.y }

  const ankleYL = legL ? legL.y + legL.h * 0.94 : (A.ankleL?.y ?? H - 35)
  const ankleYR = legR ? legR.y + legR.h * 0.94 : (A.ankleR?.y ?? H - 35)
  const ankleXL = legL ? limbX(legL, ankleYL) : (A.ankleL?.x ?? hipLPos.x)
  const ankleXR = legR ? limbX(legR, ankleYR) : (A.ankleR?.x ?? hipRPos.x)

  const kneeYL = legL ? (hipLPos.y + ankleYL) * 0.5 : (A.kneeL?.y ?? (hipPos.y + ankleYL) * 0.5)
  const kneeYR = legR ? (hipRPos.y + ankleYR) * 0.5 : (A.kneeR?.y ?? (hipPos.y + ankleYR) * 0.5)
  const kneeXL = legL ? (hipLPos.x + ankleXL) * 0.5 : (A.kneeL?.x ?? hipLPos.x)
  const kneeXR = legR ? (hipRPos.x + ankleXR) * 0.5 : (A.kneeR?.x ?? hipRPos.x)

  const footYL = Math.min(H - 5, ankleYL + 30)
  const footYR = Math.min(H - 5, ankleYR + 30)

  // 2. 装配骨骼定义表
  interface BoneSpec {
    id: BoneId
    parent: BoneId | null
    pos: { x: number; y: number }
    endPos: { x: number; y: number }
  }

  const specs: BoneSpec[] = [
    { id: 'hip', parent: null, pos: hipPos, endPos: spinePos },
    { id: 'spine', parent: 'hip', pos: spinePos, endPos: neckPos },
    { id: 'neck', parent: 'spine', pos: neckPos, endPos: headPos },
    { id: 'head', parent: 'neck', pos: headPos, endPos: { x: headPos.x, y: headPos.y - 40 } },

    { id: 'shoulderL', parent: 'spine', pos: { x: shXL, y: shYL }, endPos: { x: shXL - 10, y: shYL + 15 } },
    { id: 'upperArmL', parent: 'shoulderL', pos: { x: shXL, y: shYL }, endPos: { x: elbowXL, y: elbowYL } },
    { id: 'lowerArmL', parent: 'upperArmL', pos: { x: elbowXL, y: elbowYL }, endPos: { x: wristXL, y: wristYL } },
    { id: 'handL', parent: 'lowerArmL', pos: { x: wristXL, y: wristYL }, endPos: { x: wristXL, y: wristYL + 25 } },

    { id: 'shoulderR', parent: 'spine', pos: { x: shXR, y: shYR }, endPos: { x: shXR + 10, y: shYR + 15 } },
    { id: 'upperArmR', parent: 'shoulderR', pos: { x: shXR, y: shYR }, endPos: { x: elbowXR, y: elbowYR } },
    { id: 'lowerArmR', parent: 'upperArmR', pos: { x: elbowXR, y: elbowYR }, endPos: { x: wristXR, y: wristYR } },
    { id: 'handR', parent: 'lowerArmR', pos: { x: wristXR, y: wristYR }, endPos: { x: wristXR, y: wristYR + 25 } },

    { id: 'upperLegL', parent: 'hip', pos: hipLPos, endPos: { x: kneeXL, y: kneeYL } },
    { id: 'lowerLegL', parent: 'upperLegL', pos: { x: kneeXL, y: kneeYL }, endPos: { x: ankleXL, y: ankleYL } },
    { id: 'footL', parent: 'lowerLegL', pos: { x: ankleXL, y: ankleYL }, endPos: { x: ankleXL, y: footYL } },

    { id: 'upperLegR', parent: 'hip', pos: hipRPos, endPos: { x: kneeXR, y: kneeYR } },
    { id: 'lowerLegR', parent: 'upperLegR', pos: { x: kneeXR, y: kneeYR }, endPos: { x: ankleXR, y: ankleYR } },
    { id: 'footR', parent: 'lowerLegR', pos: { x: ankleXR, y: ankleYR }, endPos: { x: ankleXR, y: footYR } }
  ]

  const specMap = new Map<BoneId, { spec: BoneSpec; index: number }>()

  for (let i = 0; i < specs.length; i++) {
    specMap.set(specs[i]!.id, { spec: specs[i]!, index: i })
  }

  const bones: Bone[] = []

  for (let i = 0; i < specs.length; i++) {
    const s = specs[i]!
    const parentInfo = s.parent ? specMap.get(s.parent) : null
    const parentIndex = parentInfo ? parentInfo.index : null
    const parentPos = parentInfo ? parentInfo.spec.pos : { x: 0, y: 0 }

    const localX = s.pos.x - parentPos.x
    const localY = s.pos.y - parentPos.y
    const dx = s.endPos.x - s.pos.x
    const dy = s.endPos.y - s.pos.y
    const len = Math.hypot(dx, dy) || 1
    const angle = Math.atan2(dy, dx)

    const bindWorldMatrix = createMat2D()
    makeTRS(bindWorldMatrix, s.pos.x, s.pos.y, 0)
    const invBindWorldMatrix = createMat2D()
    invertMat2D(invBindWorldMatrix, bindWorldMatrix)

    const worldMatrix = createMat2D()
    copyMat2D(worldMatrix, bindWorldMatrix)
    const skinMatrix = createMat2D() // bind 状态下 skinMatrix 为恒等矩阵

    bones.push({
      id: s.id,
      index: i,
      parent: parentIndex,
      bindWorldPos: { x: s.pos.x, y: s.pos.y },
      bindWorldAngle: angle,
      length: len,
      localPos: { x: localX, y: localY },
      bindWorldMatrix,
      invBindWorldMatrix,
      localAngle: 0,
      localOffset: { x: 0, y: 0 },
      worldMatrix,
      skinMatrix
    })
  }

  const skeleton = new Skeleton(bones)
  skeleton.updateMatrices()

  return skeleton
}
