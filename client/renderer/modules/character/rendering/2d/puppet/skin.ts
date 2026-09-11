/** 2D 木偶顶点蒙皮系统（Linear Blend Skinning, LBS）
 *
 * 职责：
 * 1. 纯函数：根据图层语义与几何位置，计算每个网格顶点的骨骼绑定权重。
 * 2. 每顶点最多绑定 2 根骨骼，权重和归一化为 1.0（v' = w0 * M_skin0 * v + w1 * M_skin1 * v）。
 * 3. 严格边界防护：
 *    - 头面发组完全隔离（权重全 0，返回 null，完全由控制笼与物理弹簧接管）。
 *    - 颈部双隶属：上段跟头笼，下段跟 neck 骨骼。
 *    - 上衣袖口封套：肩外缘渐变绑定 upperArm，胸腹部严格绑 spine，防胸标拉伸。
 *    - 四肢腋下/髋部：根部 15% 渐变分配至 spine/hip，防抬手迈步撕裂破面。
 *
 * 同时导出共享给 runtime / limb-split 的工具：
 *   - HEAD_FEATURE_BNS：头面发部件集合（deform 头部分支判别）
 *   - smooth / smoothstep：clamped 立方插值
 */

import { sampleLimbWeights, smooth, smoothstep } from './limb-split'
import type { RigAnchors } from './puppet-types'
import type { BoneId, Skeleton } from './skeleton'

export { smooth, smoothstep }

export interface LayerSkin {
  boneIndices: Uint8Array // 每个顶点 2 个骨骼索引 [b0, b1, b0, b1, ...]
  weights: Float32Array // 每个顶点 2 个归一化权重 [w0, w1, w0, w1, ...], w0 + w1 = 1.0
}

/** 头部与五官部件集合（走控制笼与弹簧，不走四肢骨骼蒙皮）。neck 不在内：
 * 颈需要走 spine/neck 骨做上端跟随、下端跟衣领的 Phase 3 双隶属，详见下方 buildLayerSkin 颈分支。 */
export const HEAD_FEATURE_BNS = new Set([
  'face',
  'facedetail',
  'mouth_open',
  'mouth_close',
  'eyewhite',
  'irides',
  'eyelash',
  'eye_close',
  'nose',
  'eyebrow',
  'front hair',
  'back hair',
  'ears',
  'earwear',
  'headwear'
])

/**
 * 为单个图层构建顶点蒙皮数据
 * @param layer 图层基础属性与顶点缓存
 * @param skeleton 骨架
 * @param anchors 锚点表
 * @param canvasH 画布高度
 */
export function buildLayerSkin(
  layer: {
    name: string
    bn: string
    group: 'head' | 'body'
    side: string | null
    x: number
    y: number
    w: number
    h: number
    base: Float32Array
  },
  skeleton: Skeleton,
  anchors: RigAnchors
): LayerSkin | null {
  // 1. 头部及面部五官图层完全隔离，返回 null
  if (layer.group === 'head' || HEAD_FEATURE_BNS.has(layer.bn)) {
    return null
  }

  const nv = layer.base.length / 2

  if (nv === 0) {
    return null
  }

  const boneIndices = new Uint8Array(nv * 2)
  const weights = new Float32Array(nv * 2)

  const getBoneIndex = (id: BoneId): number => {
    const b = skeleton.getBone(id)

    return b ? b.index : 0
  }

  const hipIdx = getBoneIndex('hip')
  const spineIdx = getBoneIndex('spine')
  const neckIdx = getBoneIndex('neck')

  const upperArmLIdx = getBoneIndex('upperArmL')
  const lowerArmLIdx = getBoneIndex('lowerArmL')
  const handLIdx = getBoneIndex('handL')

  const upperArmRIdx = getBoneIndex('upperArmR')
  const lowerArmRIdx = getBoneIndex('lowerArmR')
  const handRIdx = getBoneIndex('handR')

  const upperLegLIdx = getBoneIndex('upperLegL')
  const lowerLegLIdx = getBoneIndex('lowerLegL')
  const footLIdx = getBoneIndex('footL')

  const upperLegRIdx = getBoneIndex('upperLegR')
  const lowerLegRIdx = getBoneIndex('lowerLegR')
  const footRIdx = getBoneIndex('footR')

  const NP = anchors.neckPivot
  const faceW = anchors.face.x1 - anchors.face.x0
  const faceH = anchors.face.y1 - anchors.face.y0
  const hipY = skeleton.bones[hipIdx]!.bindWorldPos.y

  const bn = layer.bn

  // 辅助函数：设置单一顶点的 2 根骨骼与权重并归一化
  const setV = (v: number, b0: number, w0: number, b1: number, w1: number): void => {
    const sum = w0 + w1 || 1
    boneIndices[v * 2] = b0
    boneIndices[v * 2 + 1] = b1
    weights[v * 2] = w0 / sum
    weights[v * 2 + 1] = w1 / sum
  }

  // 2. 颈部：上端跟头（由 deform 控制笼处理），下端跟 neck/spine。
  //    t=0 (顶端) → neckIdx 权重 0，全部交给头笼；t=1 (底端) → 全 spine，避免
  //    同时被笼和骨骼双驱动造成的颈段拉伸。
  if (bn === 'neck') {
    for (let v = 0; v < nv; v++) {
      const y = layer.base[v * 2 + 1]!
      const t = smoothstep(anchors.neckTop, anchors.neckBottom, y)
      setV(v, neckIdx, t * 0.6, spineIdx, 1 - t * 0.6)
    }

    return { boneIndices, weights }
  }

  // 3. 上衣（topwear）：躯干主绑 spine 与 hip，两侧袖口外缘按封套兼绑 upperArm
  if (bn === 'topwear') {
    const shoulderY = Math.max(
      skeleton.bones[upperArmLIdx]!.bindWorldPos.y,
      skeleton.bones[upperArmRIdx]!.bindWorldPos.y
    )

    const sleeveBottom = shoulderY + faceH

    for (let v = 0; v < nv; v++) {
      const x = layer.base[v * 2]!
      const y = layer.base[v * 2 + 1]!
      const armIdx = x < NP.cx ? upperArmLIdx : upperArmRIdx
      const shoulder = skeleton.bones[armIdx]!.bindWorldPos
      const outward = (x - shoulder.x) * (x < NP.cx ? -1 : 1)

      const sleeve =
        smoothstep(-faceW * 0.2, faceW * 0.45, outward) *
        smoothstep(shoulder.y - faceH * 0.4, shoulder.y + faceH * 0.1, y) *
        (1 - smoothstep(shoulder.y + faceH * 0.35, sleeveBottom, y))

      // 袖口影响止于肩部附近；连衣裙下摆不能被抬手带走。
      const hipWeight = smoothstep(sleeveBottom, Math.max(sleeveBottom + faceH, hipY), y) * 0.5

      if (sleeve > 0) {
        setV(v, spineIdx, 1 - sleeve * 0.8, armIdx, sleeve * 0.8)
      } else {
        setV(v, spineIdx, 1 - hipWeight, hipIdx, hipWeight)
      }
    }

    return { boneIndices, weights }
  }

  // 4. 手臂与手套（handwear / arm / arms）
  if (bn === 'handwear' || bn === 'arm' || bn === 'arms' || bn === 'arm-upper' || bn === 'arm-lower' || bn === 'hand') {
    const isL = layer.x + layer.w / 2 < NP.cx
    const upperArmIdx = isL ? upperArmLIdx : upperArmRIdx
    const lowerArmIdx = isL ? lowerArmLIdx : lowerArmRIdx
    const handIdx = isL ? handLIdx : handRIdx

    const cuts = {
      joint0: skeleton.bones[upperArmIdx]!.bindWorldPos.y,
      joint1: skeleton.bones[lowerArmIdx]!.bindWorldPos.y,
      joint2: skeleton.bones[handIdx]!.bindWorldPos.y
    }

    for (let v = 0; v < nv; v++) {
      const y = layer.base[v * 2 + 1]!
      const { rootWeight, upperWeight, lowerWeight, endWeight } = sampleLimbWeights(y, cuts, true)

      if (rootWeight > 0) {
        // 肩部与腋下过渡带：上臂 + 脊柱
        setV(v, upperArmIdx, upperWeight, spineIdx, rootWeight)
      } else if (endWeight > 0) {
        // 手/脚主导：下臂 + 手
        setV(v, lowerArmIdx, lowerWeight + upperWeight, handIdx, endWeight)
      } else {
        setV(v, upperArmIdx, upperWeight, lowerArmIdx, lowerWeight)
      }
    }

    return { boneIndices, weights }
  }

  // 5. 裙装/下装（bottomwear）：腰线以上绑 hip / spine，下摆绑 hip
  if (bn === 'bottomwear') {
    for (let v = 0; v < nv; v++) {
      const y = layer.base[v * 2 + 1]!
      const t = smoothstep(hipY - 30, hipY + 40, y)
      // 腰线附近上部微跟 spine，裙身全部绑 hip
      setV(v, hipIdx, 0.7 + 0.3 * t, spineIdx, 0.3 * (1 - t))
    }

    return { boneIndices, weights }
  }

  // 6. 腿部与裤装（legwear / legs / leg / leg-upper / leg-lower）
  if (bn === 'legwear' || bn === 'legs' || bn === 'leg' || bn === 'leg-upper' || bn === 'leg-lower') {
    const isL = layer.x + layer.w / 2 < NP.cx
    const upperLegIdx = isL ? upperLegLIdx : upperLegRIdx
    const lowerLegIdx = isL ? lowerLegLIdx : lowerLegRIdx
    const footIdx = isL ? footLIdx : footRIdx

    const cuts = {
      joint0: skeleton.bones[upperLegIdx]!.bindWorldPos.y,
      joint1: skeleton.bones[lowerLegIdx]!.bindWorldPos.y,
      joint2: skeleton.bones[footIdx]!.bindWorldPos.y
    }

    for (let v = 0; v < nv; v++) {
      const y = layer.base[v * 2 + 1]!
      const { rootWeight, upperWeight, lowerWeight, endWeight } = sampleLimbWeights(y, cuts, true)

      if (rootWeight > 0) {
        // 髋关节过渡带：大腿 + 髋
        setV(v, upperLegIdx, upperWeight, hipIdx, rootWeight)
      } else if (endWeight > 0) {
        // 脚主导：小腿 + 脚
        setV(v, lowerLegIdx, lowerWeight + upperWeight, footIdx, endWeight)
      } else {
        setV(v, upperLegIdx, upperWeight, lowerLegIdx, lowerWeight)
      }
    }

    return { boneIndices, weights }
  }

  // 7. 鞋履（footwear / foot）
  if (bn === 'footwear' || bn === 'foot') {
    const isL = layer.x + layer.w / 2 < NP.cx
    const lowerLegIdx = isL ? lowerLegLIdx : lowerLegRIdx
    const footIdx = isL ? footLIdx : footRIdx

    for (let v = 0; v < nv; v++) {
      const y = layer.base[v * 2 + 1]!
      const ankleY = skeleton.bones[footIdx]!.bindWorldPos.y
      const span = Math.max(10, (ankleY - skeleton.bones[lowerLegIdx]!.bindWorldPos.y) * 0.25)
      const t = smoothstep(ankleY - span, ankleY + span, y)
      setV(v, footIdx, t, lowerLegIdx, 1 - t)
    }

    return { boneIndices, weights }
  }

  // 8. 身体其它图层（body / torso / skin）保底
  for (let v = 0; v < nv; v++) {
    const y = layer.base[v * 2 + 1]!
    const t = smoothstep(NP.cy, hipY, y)
    setV(v, spineIdx, 1 - t, hipIdx, t)
  }

  return { boneIndices, weights }
}
