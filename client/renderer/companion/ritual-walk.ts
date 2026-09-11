import { clamp, sleep } from '@runtime'

import { $screenLocked } from '@/companion/activity'
import { $gazeTarget, $spriteAction, lockGazeToPoint, setSpriteState } from '@/companion/companion-store'
import { speakProactive } from '@/companion/proactive/proactive'
import {
  $defaultScale,
  $spatialPos,
  $spatialScale,
  computePerchPlacement,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  moveTo,
  updateSpatialDecision
} from '@/companion/spatial'
import { $chatVisible } from '@/shared/store/chat-visibility'

const RETRY_MS = 300
const RETRY_COUNT = 5
// DESIGN §3.6：远距离飞、近距离走。低于该距离的目标走过去更有"走过去动手"的仪式感。
const WALK_RANGE_PX = 400

// 仪式行走失败的离线/机械降级台词（DESIGN §3.6 / RULES 原则七边界）——
// 走 speakProactive 的档位门控：静止档静默、常规档仅气泡、自主档开口。
const TARGET_LOST_LINES = ['咦…我没找到那个窗口，先直接试试吧。', '那个窗口在哪呀…我先直接试。']
const PERCH_TIGHT_LINES = ['这边好挤，我够不着…先直接试试吧。']

function pickLine(pool: readonly string[]): string {
  return pool[Math.floor(Math.random() * pool.length)] ?? pool[0]!
}

interface WindowGeom {
  x: number
  y: number
  w: number
  h: number
}

export type { WindowGeom }

export async function findWindowByKeyword(keyword: string): Promise<WindowGeom | null> {
  // 空关键词会让 `name.includes('')` 恒真——匹配到枚举出的第一个窗口，
  // 精灵会对着一个无关窗口走过去并点它。关键词缺失 = 找不到目标。
  if (!keyword.trim() || !window.spiritagent?.runnerInvoke) {
    return null
  }

  try {
    const result = await window.spiritagent.runnerInvoke('system.get_windows', {})
    const windows = (result as { windows?: Array<{ name: string; title: string } & WindowGeom> }).windows ?? []
    const kw = keyword.toLowerCase()

    const match = windows.find(
      w =>
        w.name.toLowerCase().includes(kw) ||
        w.title.toLowerCase().includes(kw) ||
        kw.includes(w.name.toLowerCase().split('.')[0])
    )

    return match ? { x: match.x, y: match.y, w: match.w, h: match.h } : null
  } catch {
    return null
  }
}

/** 屏幕坐标 → 精灵窗口归一 [-1,1] 的视线目标。粗粒度方向感即可，clamp 到边界防越轴。 */
export function gazeTowardsPoint(point: { x: number; y: number }): { nx: number; ny: number } {
  const pos = $spatialPos.get()
  const halfW = (getBaseSpriteWidth() * $spatialScale.get()) / 2
  const halfH = (getBaseSpriteHeight() * $spatialScale.get()) / 2

  return {
    nx: clamp((point.x - (pos.x + halfW)) / Math.max(halfW, 1), -1, 1),
    ny: clamp((point.y - (pos.y + halfH)) / Math.max(halfH, 1), -1, 1)
  }
}

export async function performRitualWalk<T>(
  findTarget: () => Promise<WindowGeom | null>,
  execute: () => Promise<T>,
  opts?: { previewClick?: boolean }
): Promise<T> {
  if ($chatVisible.get() || $screenLocked.get()) {
    return execute()
  }

  let geom = await findTarget()

  for (let attempt = 0; !geom && attempt < RETRY_COUNT; attempt++) {
    await sleep(RETRY_MS)
    geom = await findTarget()
  }

  if (!geom) {
    void speakProactive(pickLine(TARGET_LOST_LINES))

    return execute()
  }

  // 栖身落位与 events / autonomy 同规则：以用户默认比例为缩身上限。
  const perch = computePerchPlacement(geom, $defaultScale.get())?.pos ?? null

  if (!perch) {
    void speakProactive(pickLine(PERCH_TIGHT_LINES))

    return execute()
  }

  // 飞行途中视线锁定目标窗口中心；抵达后按方位抬手指向，再接 click 触碰
  const targetCenter = { x: geom.x + geom.w / 2, y: geom.y + geom.h / 2 }
  lockGazeToPoint(gazeTowardsPoint(targetCenter))

  try {
    const dist = Math.hypot(perch.x - $spatialPos.get().x, perch.y - $spatialPos.get().y)
    await new Promise<void>(resolve => moveTo(perch, dist > WALK_RANGE_PX ? 'fly' : 'walk', resolve))

    const dx = targetCenter.x - ($spatialPos.get().x + getBaseSpriteWidth() / 2)
    $spriteAction.set(dx >= 0 ? 'point_right' : 'point_left')
    await sleep(800)

    // DESIGN §3.6：抵达后播放专属「点击/触碰」肢体动作，
    // 与通用 interacting 状态区别开来——动作优先于状态动画。
    $spriteAction.set('click')
    setSpriteState('interacting', { durationMs: 1500 })

    // 预点击只对「点击不是工具本体」的仪式有意义（open_application 聚焦已开窗口）。
    // click_at 工具本身就是要执行的那次点击——再补一次就是双击。
    if (opts?.previewClick !== false && window.spiritagent?.runnerInvoke) {
      window.spiritagent
        .runnerInvoke('system.click_at', { x: Math.round(targetCenter.x), y: Math.round(targetCenter.y) })
        .catch(() => {})
    }

    await sleep(400)

    const result = await execute()

    // 执行结束后清除 click action，让后续状态机正常推进
    if ($spriteAction.get() === 'click') {
      $spriteAction.set(null)
    }

    return result
  } finally {
    // gaze 泄漏会让精灵永远盯着最后的目标；异常路径同样要解锁
    $gazeTarget.set(null)
    await sleep(800)
    updateSpatialDecision()
  }
}
