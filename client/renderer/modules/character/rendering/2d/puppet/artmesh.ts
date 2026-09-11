/** ArtMesh — alpha 轮廓贴形的三角剖分网格（Phase 2，替代每层规则网格）。
 *
 * 采样：边界点（alpha 边缘、固定 stride）+ 内部点（约 maxEdge 间距、仅域内）；
 * 剖分：增量 Delaunay（Bowyer-Watson）；域外三角形按质心/边中点 alpha 剔除——
 * 片元着色器本就按 alpha discard，轻微越域不可见，故无需约束恢复的完整 CDT。
 * 收益：顶点密度贴合实际轮廓（发梢/睫毛/下巴缘），大形变下边缘清晰不糊。
 */

import type { RigImage } from './puppet-types'

const ALPHA_TH = 12
const BOUNDARY_STRIDE = 2

export interface ArtMesh {
  /** 顶点 xy（图像局部像素坐标） */
  verts: Float32Array
  tris: Uint16Array
  stats: { verts: number; tris: number; cover: number }
}

interface DelaTri {
  a: number
  b: number
  c: number
  cx: number
  cy: number
  r2: number
}

function circumTri(a: number, b: number, c: number, px: Float64Array, py: Float64Array): DelaTri | null {
  const ax = px[a]!
  const ay = py[a]!
  const bx = px[b]!
  const by = py[b]!
  const cxp = px[c]!
  const cyp = py[c]!
  const d = 2 * (ax * (by - cyp) + bx * (cyp - ay) + cxp * (ay - by))

  if (Math.abs(d) < 1e-9) {
    return null
  }

  const a2 = ax * ax + ay * ay
  const b2 = bx * bx + by * by
  const c2 = cxp * cxp + cyp * cyp
  const ux = (a2 * (by - cyp) + b2 * (cyp - ay) + c2 * (ay - by)) / d
  const uy = (a2 * (cxp - bx) + b2 * (ax - cxp) + c2 * (bx - ax)) / d
  const dx = ax - ux
  const dy = ay - uy

  return { a, b, c, cx: ux, cy: uy, r2: dx * dx + dy * dy }
}

/** 增量 Delaunay（Bowyer-Watson）。px/py 需预留 3 个槽位给超级三角形，nReal 为真实点数。 */
function delaunay(px: Float64Array, py: Float64Array, nReal: number): DelaTri[] {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity

  for (let i = 0; i < nReal; i++) {
    minX = Math.min(minX, px[i]!)
    minY = Math.min(minY, py[i]!)
    maxX = Math.max(maxX, px[i]!)
    maxY = Math.max(maxY, py[i]!)
  }

  const span = Math.max(maxX - minX, maxY - minY, 1) * 20
  const mIdx = nReal
  px[mIdx] = (minX + maxX) / 2 - span
  py[mIdx] = (minY + maxY) / 2
  px[mIdx + 1] = (minX + maxX) / 2 + span
  py[mIdx + 1] = (minY + maxY) / 2 - span
  px[mIdx + 2] = (minX + maxX) / 2
  py[mIdx + 2] = (minY + maxY) / 2 + span

  let tris: DelaTri[] = []

  const t0 = circumTri(mIdx, mIdx + 1, mIdx + 2, px, py)
  tris.push(t0!)

  for (let i = 0; i < nReal; i++) {
    const x = px[i]!
    const y = py[i]!
    const bad: DelaTri[] = []

    for (const t of tris) {
      const dx = x - t.cx
      const dy = y - t.cy

      if (dx * dx + dy * dy <= t.r2) {
        bad.push(t)
      }
    }

    const edgeCount = new Map<number, number>()
    const edgeOrder: number[] = []

    for (const t of bad) {
      for (const [u, v] of [
        [t.a, t.b],
        [t.b, t.c],
        [t.c, t.a]
      ] as const) {
        const key = u < v ? u * 16777216 + v : v * 16777216 + u
        const cnt = edgeCount.get(key) ?? 0
        edgeCount.set(key, cnt + 1)
        edgeOrder.push(key, u, v)
      }
    }

    const badSet = new Set(bad)
    const next: DelaTri[] = tris.filter(t => !badSet.has(t))

    for (let e = 0; e < edgeOrder.length; e += 3) {
      const key = edgeOrder[e]!

      if (edgeCount.get(key) === 1) {
        const u = edgeOrder[e + 1]!
        const v = edgeOrder[e + 2]!
        const nt = circumTri(u, v, i, px, py)

        if (nt) {
          next.push(nt)
        }
      }
    }

    tris = next
  }

  return tris
}

export function buildArtMesh(img: RigImage, maxEdge: number): ArtMesh | null {
  const { width: w, height: h, data } = img
  const mask = new Uint8Array(w * h)

  for (let i = 0; i < w * h; i++) {
    mask[i] = data[i * 4 + 3]! > ALPHA_TH ? 1 : 0
  }

  const alphaAt = (x: number, y: number): number => {
    if (x < 0 || y < 0 || x >= w || y >= h) {
      return 0
    }

    return data[(y * w + x) * 4 + 3]!
  }

  // 边界点：域内且 stride 邻域至少一个域外
  const bxs: number[] = []
  const bys: number[] = []

  for (let y = 0; y < h; y += BOUNDARY_STRIDE) {
    for (let x = 0; x < w; x += BOUNDARY_STRIDE) {
      if (!mask[y * w + x]) {
        continue
      }

      const edge =
        x < BOUNDARY_STRIDE ||
        y < BOUNDARY_STRIDE ||
        x + BOUNDARY_STRIDE >= w ||
        y + BOUNDARY_STRIDE >= h ||
        !mask[y * w + x - BOUNDARY_STRIDE] ||
        !mask[y * w + x + BOUNDARY_STRIDE] ||
        !mask[(y - BOUNDARY_STRIDE) * w + x] ||
        !mask[(y + BOUNDARY_STRIDE) * w + x]

      if (edge) {
        bxs.push(x)
        bys.push(y)
      }
    }
  }

  // 内部点：约 maxEdge 间距的抖动网格，仅取严格域内，并与边界点保持最小间距
  const spacing = Math.max(4, Math.round(maxEdge * 0.9))
  const minDist2 = (spacing * 0.45) ** 2
  const xs: number[] = [...bxs]
  const ys: number[] = [...bys]

  for (let gy = spacing; gy < h - 1; gy += spacing) {
    for (let gx = spacing; gx < w - 1; gx += spacing) {
      const jx = Math.round(gx + spacing * 0.3 * ((gy / spacing) % 2))
      const jy = gy
      let ok = true

      for (let dyy = -2; dyy <= 2 && ok; dyy += 4) {
        for (let dxx = -2; dxx <= 2 && ok; dxx += 4) {
          if (alphaAt(jx + dxx, jy + dyy) <= ALPHA_TH) {
            ok = false
          }
        }
      }

      if (!ok) {
        continue
      }

      for (let i = 0; i < bxs.length; i++) {
        const ddx = bxs[i]! - jx
        const ddy = bys[i]! - jy

        if (ddx * ddx + ddy * ddy < minDist2) {
          ok = false

          break
        }
      }

      if (ok) {
        xs.push(jx)
        ys.push(jy)
      }
    }
  }

  if (xs.length < 3) {
    return null
  }

  const px = new Float64Array(xs.length + 3)
  const py = new Float64Array(xs.length + 3)

  for (let i = 0; i < xs.length; i++) {
    px[i] = xs[i]!
    py[i] = ys[i]!
  }

  const nReal = xs.length
  const trisAll = delaunay(px, py, nReal)
  const kept: DelaTri[] = []
  let coverHit = 0

  for (const t of trisAll) {
    if (t.a >= nReal || t.b >= nReal || t.c >= nReal) {
      continue
    }

    const ax = px[t.a]!
    const ay = py[t.a]!
    const bx = px[t.b]!
    const by = py[t.b]!
    const cxp = px[t.c]!
    const cyp = py[t.c]!
    const mx = (ax + bx + cxp) / 3
    const my = (ay + by + cyp) / 3

    // 质心 + 三边中点任一命中即保留（覆盖细线结构，越域部分被 alpha discard 遮蔽）
    const hit =
      alphaAt(Math.round(mx), Math.round(my)) > ALPHA_TH ||
      alphaAt(Math.round((ax + bx) / 2), Math.round((ay + by) / 2)) > ALPHA_TH ||
      alphaAt(Math.round((bx + cxp) / 2), Math.round((by + cyp) / 2)) > ALPHA_TH ||
      alphaAt(Math.round((cxp + ax) / 2), Math.round((cyp + ay) / 2)) > ALPHA_TH

    if (hit) {
      kept.push(t)

      if (alphaAt(Math.round(mx), Math.round(my)) > ALPHA_TH) {
        coverHit++
      }
    }
  }

  if (!kept.length) {
    return null
  }

  // 压缩顶点表（仅保留被引用顶点）
  const remap = new Int32Array(nReal).fill(-1)
  let nv = 0

  for (const t of kept) {
    for (const vi of [t.a, t.b, t.c]) {
      if (remap[vi]! < 0) {
        remap[vi] = nv++
      }
    }
  }

  const verts = new Float32Array(nv * 2)
  const triIdx = new Uint16Array(kept.length * 3)
  let k = 0

  for (const t of kept) {
    triIdx[k++] = remap[t.a]!
    triIdx[k++] = remap[t.b]!
    triIdx[k++] = remap[t.c]!
  }

  for (let i = 0; i < nReal; i++) {
    const m = remap[i]!

    if (m >= 0) {
      verts[m * 2] = xs[i]!
      verts[m * 2 + 1] = ys[i]!
    }
  }

  return {
    verts,
    tris: triIdx,
    stats: { verts: nv, tris: kept.length, cover: kept.length ? coverHit / kept.length : 0 }
  }
}
