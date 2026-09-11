import type { ParametricModel } from './parametric'

export interface EdgePose {
  width: number
  height: number
  contactX: number
  head: [number, number, number, number]
  hands: [[number, number, number, number], [number, number, number, number]] | null
  bounds: [number, number, number, number]
  textures: Record<'body' | 'closed', { key: string; hash: string; url: string }>
}

export interface EdgePosePack {
  schema: 'spiritagent.2d.poses/1'
  left: EdgePose
  right: EdgePose
}

export function parseEdgePoses(value: unknown, urls: Record<string, string>): EdgePosePack | null {
  if (!value || typeof value !== 'object') {
    return null
  }

  const pack = value as EdgePosePack

  if (pack.schema !== 'spiritagent.2d.poses/1') {
    return null
  }

  const result = { schema: pack.schema } as EdgePosePack

  for (const side of ['left', 'right'] as const) {
    const pose = pack[side]

    if (
      !pose ||
      pose.width !== 1024 ||
      pose.height !== 1024 ||
      !Number.isFinite(pose.contactX) ||
      pose.contactX < 100 ||
      pose.contactX > 924
    ) {
      return null
    }

    for (const rect of [pose.head, pose.bounds]) {
      if (
        !Array.isArray(rect) ||
        rect.length !== 4 ||
        !rect.every(p => Number.isFinite(p) && p >= 0 && p <= 1024) ||
        rect[0] >= rect[2] ||
        rect[1] >= rect[3]
      ) {
        return null
      }
    }

    const textures = {} as EdgePose['textures']

    for (const name of ['body', 'closed'] as const) {
      const texture = pose.textures?.[name]

      if (!texture || typeof texture.key !== 'string' || !/^[a-f0-9]{64}$/.test(texture.hash) || !urls[texture.key]) {
        return null
      }

      textures[name] = { ...texture, url: urls[texture.key]! }
    }

    const hands =
      Array.isArray(pose.hands) &&
      pose.hands.length === 2 &&
      pose.hands.every(
        rect =>
          Array.isArray(rect) &&
          rect.length === 4 &&
          rect.every(p => Number.isFinite(p) && p >= 0 && p <= 1024) &&
          rect[0] < rect[2] &&
          rect[1] < rect[3]
      )
        ? pose.hands
        : null

    result[side] = { ...pose, hands, textures }
  }

  return result
}

export function createEdgePoseModel(pose: EdgePose, side: 'left' | 'right'): ParametricModel {
  const vertices: number[] = [],
    uvs: number[] = [],
    triangles: number[] = []

  const cols = 64

  for (let y = 0; y <= cols; y++) {
    for (let x = 0; x <= cols; x++) {
      vertices.push((x * pose.width) / cols, (y * pose.height) / cols)
      uvs.push(x / cols, y / cols)

      if (x < cols && y < cols) {
        const a = y * (cols + 1) + x
        triangles.push(a, a + 1, a + cols + 1, a + 1, a + cols + 2, a + cols + 1)
      }
    }
  }

  const zeros = vertices.map(() => 0)
  const amplitude = pose.hands ? (pose.head[3] - pose.head[1]) * 0.08 : 0

  const offsets = (amount: number): number[] =>
    vertices.map((_, i) => {
      const x = vertices[i - (i % 2)]!
      const y = vertices[i - (i % 2) + 1]!
      let weight = Math.max(0, Math.min(1, (pose.head[3] + 30 - y) / (pose.head[3] - pose.head[1])))

      // 手可高于脸；按资产中的双手区域固定接触点，边界外平滑衰减。
      for (const hand of pose.hands ?? []) {
        const distance = Math.max(hand[0] - x, x - hand[2], hand[1] - y, y - hand[3], 0)
        weight *= Math.max(0, Math.min(1, (distance - 16) / 24))
      }

      return i % 2 === 0 ? amount * (side === 'left' ? 1 : -1) * weight : -Math.abs(amount) * 0.12 * weight
    })

  return {
    width: pose.width,
    height: pose.height,
    parameters: {
      peek: { min: -1, max: 1, initial: 0, smoothing: 0.12 },
      blink: { min: 0, max: 1, initial: 0, smoothing: 0 }
    },
    layers: (['body', 'closed'] as const).map((name, i) => ({
      id: name,
      texture: name,
      vertices,
      uvs,
      triangles,
      opacity: i ? 0 : 1,
      order: i,
      shapes: {
        peek: [
          { value: -1, offsets: offsets(-amplitude) },
          { value: 0, offsets: zeros },
          { value: 1, offsets: offsets(amplitude) }
        ],
        ...(i
          ? {
              blink: [
                { value: 0, offsets: zeros, opacityDelta: 0 },
                { value: 1, offsets: zeros, opacityDelta: 1 }
              ]
            }
          : {})
      }
    }))
  }
}
