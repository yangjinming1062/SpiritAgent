/** HeadCage — 脸面/头骨双表面三角控制笼（Phase 2）+ 伪 3D 转头几何（Phase 3）。
 *
 * 三个语义控制点（左颊 / 右颊 / 颅顶）+ 脸面深度 dF 与头骨深度 dS；
 * 每顶点绑定 = 笼三角形重心坐标 cb[3] + 脸面↔头骨混合 μ（dEff = μ·dF + (1-μ)·dS）。
 * Phase 3 增补圆投影几何：Rf/Rs 为脸面/头骨横向半径（逐顶点按 μ 插值得 hR），
 * rv 为俯仰纵向半径；cy/cv 为六点脸面深度曲线（额头→下巴的锚点 y 与前向深度增量），
 * 非中立姿态补充表面 Z 深度，中立姿态严格保持原图。
 */

import { clamp } from '@runtime'

import type { RigAnchors } from './puppet-types'

export interface HeadCage {
  px: [number, number, number]
  py: [number, number, number]
  dF: number
  dS: number
  /** 脸面横向半径（眼线高度脸宽一半） */
  rf: number
  /** 头骨横向半径（头部组层实测最大半径） */
  rs: number
  /** 俯仰纵向半径（头半高近似） */
  rv: number
  /** 六点深度曲线锚点 y（升序：额/鼻梁/鼻尖/上唇/下唇/下巴） */
  cy: number[]
  /** 对应前向深度增量（dd 单位） */
  cv: number[]
}

/** 六点脸面深度曲线默认值（可编辑锚点的解析缺省，靠前部位转/俯时移动更多）。 */
const DEPTH_CURVE = [0.08, 0.24, 0.38, 0.27, 0.21, 0.06]

/** 从语义锚点推导三角控制笼；rs 为头部组层实测最大横向半径。 */
export function buildHeadCage(A: RigAnchors, dF: number, dS: number, rs: number): HeadCage {
  const fw = A.face.x1 - A.face.x0
  const fh = A.face.y1 - A.face.y0
  const eyeY = A.eyeL ? (A.eyeL.y0 + A.eyeL.y1) / 2 : A.face.cy
  const eB = A.eyeL ? A.eyeL.y1 : A.face.y0 + fh * 0.45
  const mT = A.mouth.y0
  const rf = fw / 2

  return {
    px: [A.face.x0 + fw * 0.06, A.face.x1 - fw * 0.06, A.face.cx],
    py: [eyeY, eyeY, A.face.y0 + fh * 0.08],
    dF,
    dS,
    rf,
    rs: Math.max(rs, rf * 1.08),
    rv: fh * 0.75,
    cy: [A.face.y0 + fh * 0.06, eB + 0.35 * (mT - eB), eB + 0.68 * (mT - eB), A.mouth.y0, A.mouth.y1, A.face.y1],
    cv: [...DEPTH_CURVE]
  }
}

/** 六点深度曲线取值（分段线性，端点外取端值）。 */
export function curveDepth(cage: HeadCage, y: number): number {
  const { cy, cv } = cage

  if (y <= cy[0]!) {
    return cv[0]!
  }

  for (let i = 1; i < cy.length; i++) {
    if (y <= cy[i]!) {
      const t = (y - cy[i - 1]!) / Math.max(1e-6, cy[i]! - cy[i - 1]!)

      return cv[i - 1]! + t * (cv[i]! - cv[i - 1]!)
    }
  }

  return cv[cv.length - 1]!
}

/** 顶点在笼三角形中的重心坐标（三角形外为外插；仿射位移场下外插仍精确）。 */
export function cageBary(cage: HeadCage, x: number, y: number, out: Float32Array, o: number): void {
  const [x0, x1, x2] = cage.px
  const [y0, y1, y2] = cage.py
  const det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
  const w1 = ((x - x0) * (y2 - y0) - (x2 - x0) * (y - y0)) / det
  const w2 = ((x2 - x0) * (y - y0) - (x - x0) * (y1 - y0)) / det
  out[o] = 1 - w1 - w2
  out[o + 1] = w1
  out[o + 2] = w2
}

/** 脸面↔头骨混合 μ：按层深度定位（1=纯脸面、0=纯头骨）。μ_base 使 dEff≈原层 depth，保证回归基线。 */
export function headBlendMu(dF: number, dS: number, dd: number): number {
  if (dS - dF < 1e-6) {
    return 1
  }

  return clamp((dS - dd) / (dS - dF), 0, 1)
}
