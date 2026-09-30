import { sleep } from '@runtime'

import { log } from '@/shared/lib/log'

import { isActionStageVisible, observeActionStageVisibility } from './actions'
import { setSpriteState } from './companion-store'
import { speakProactiveLine } from './proactive-speak'
import {
  $defaultScale,
  $spatialPos,
  computePerchPlacement,
  locomotionForDistance,
  moveDurationMs,
  setSpatialLocale,
  updateSpatialDecision
} from './spatial'
import { $spriteGesture, clearSpriteGesture, playSpriteGesture } from './sprite/gesture'

const RETRY_MS = 300
const RETRY_COUNT = 5
// 行走动画被中止（生活空间打开 / 开始拖拽会取消移动且不回调）时的宽限：
// 到点即视为行走结束，就地继续执行原工具。
const WALK_ABORT_GRACE_MS = 2000

// 仪式行走失败的离线/机械降级台词（DESIGN「仪式性行走」 / RULES 原则七边界）——
// 走 speakProactive 的档位门控：静止档静默、常规档仅气泡、自主档开口。
const TARGET_LOST_LINES = ['咦…我没找到那个窗口，先直接试试吧。', '那个窗口在哪呀…我先直接试。'] as const
const PERCH_TIGHT_LINES = ['这边好挤，我够不着…先直接试试吧。'] as const

function pickLine(pool: readonly [string, ...string[]]): string {
  return pool[Math.floor(Math.random() * pool.length)] ?? pool[0]
}

// 行走或指向期间舞台变为不可见时立刻结束等待（隐藏会取消移动且不触发到达回调）。
function waitUntilHidden(): { hidden: Promise<false>; stop: () => void } {
  let stop: () => void = () => {}

  const hidden = new Promise<false>(resolve => {
    stop = observeActionStageVisibility(visible => {
      if (!visible) {
        resolve(false)
      }
    })
  })

  return { hidden, stop }
}

interface WindowGeom {
  x: number
  y: number
  w: number
  h: number
}

export type { WindowGeom }

interface RunnerWindow extends WindowGeom {
  name: string
  title: string
}

function isRunnerWindow(value: unknown): value is RunnerWindow {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const w = value as Partial<Record<keyof RunnerWindow, unknown>>

  return (
    typeof w.name === 'string' &&
    typeof w.title === 'string' &&
    Number.isFinite(w.x) &&
    Number.isFinite(w.y) &&
    Number.isFinite(w.w) &&
    Number.isFinite(w.h)
  )
}

export async function findWindowByKeyword(keyword: string): Promise<WindowGeom | null> {
  // 空关键词会让 `name.includes('')` 恒真——匹配到枚举出的第一个窗口，
  // 精灵会对着一个无关窗口走过去并点它。关键词缺失 = 找不到目标。
  if (!keyword.trim() || !window.spiritagent?.runnerInvoke) {
    return null
  }

  let result: unknown

  try {
    result = await window.spiritagent.runnerInvoke('system.get_windows', {})
  } catch (error) {
    log.warn('ritual-walk', 'system.get_windows failed', error)

    return null
  }

  const listed = typeof result === 'object' && result !== null && 'windows' in result ? result.windows : null
  const windows = Array.isArray(listed) ? listed.filter(isRunnerWindow) : []
  const kw = keyword.toLowerCase()

  const match = windows.find(w => {
    const name = w.name.toLowerCase()
    // name 主干为空时不参与反向包含匹配，否则 `kw.includes('')` 恒真。
    const stem = name.split('.')[0]

    return name.includes(kw) || w.title.toLowerCase().includes(kw) || (stem !== '' && kw.includes(stem))
  })

  return match ? { x: match.x, y: match.y, w: match.w, h: match.h } : null
}

// Runner 给出原生屏幕坐标；落位与指向须用主进程换算后的精灵视口坐标，换算失败按定位不明处理。
async function toViewportRect(geom: WindowGeom): Promise<WindowGeom | null> {
  try {
    return await window.spiritagent.sprite.mapScreenRect(geom)
  } catch (error) {
    log.warn('ritual-walk', 'Could not map target into the sprite viewport', error)

    return null
  }
}

// 仪式只在精灵舞台实际可见时进行（与表达播放同一判断：精灵窗未隐藏或最小化、未被完整入口收起、
// 未开轻语、未锁屏），每一步行动前重验；不可见时不走动、不出声、不预点击，直接执行原工具。
export async function performRitualWalk<T>(
  findTarget: () => Promise<WindowGeom | null>,
  execute: () => Promise<T>,
  opts?: { previewClick?: boolean }
): Promise<T> {
  if (!isActionStageVisible()) {
    return execute()
  }

  let geom = await findTarget()

  for (let attempt = 0; !geom && attempt < RETRY_COUNT && isActionStageVisible(); attempt++) {
    await sleep(RETRY_MS)
    geom = await findTarget()
  }

  const view = geom && isActionStageVisible() ? await toViewportRect(geom) : null

  // 查找与换算期间舞台变为不可见：不再出声或走动，直接执行原工具。
  if (!isActionStageVisible()) {
    return execute()
  }

  if (!geom || !view) {
    void speakProactiveLine(pickLine(TARGET_LOST_LINES))

    return execute()
  }

  const targetCenter = { x: view.x + view.w / 2, y: view.y + view.h / 2 }

  // 目标不在精灵所在显示器内时走不过去，与栖身空间不足同样处理。
  const inViewport =
    targetCenter.x >= 0 &&
    targetCenter.x < window.innerWidth &&
    targetCenter.y >= 0 &&
    targetCenter.y < window.innerHeight

  // 栖身落位与 events / autonomy 同规则：以用户默认比例为缩身上限。
  const perch = inViewport ? (computePerchPlacement(view, $defaultScale.get())?.pos ?? null) : null

  if (!perch) {
    void speakProactiveLine(pickLine(PERCH_TIGHT_LINES))

    return execute()
  }

  let cueSeq: number | null = null
  const stage = waitUntilHidden()

  try {
    const dist = Math.hypot(perch.x - $spatialPos.get().x, perch.y - $spatialPos.get().y)
    const locomotion = locomotionForDistance(dist)

    // 到达回调在行走被取消时不会触发（spatial 的收起、隐藏与拖拽中止路径直接丢弃它）；
    // 仪式行走只是装饰，限时等待后必须继续执行原工具，不能让行走挂起整条工具链。
    const arrived = await Promise.race([
      new Promise<boolean>(resolve =>
        setSpatialLocale('perch', { position: perch, locomotion, onArrive: () => resolve(true) })
      ),
      sleep(moveDurationMs(dist, locomotion) + WALK_ABORT_GRACE_MS).then(() => false),
      stage.hidden
    ])

    // 行走未抵达（被拖拽、收起、隐藏或锁屏打断）时不再指向或预点击，直接执行原工具。
    if (!arrived) {
      return await execute()
    }

    // DESIGN「仪式性行走」：抵达后指向目标，再以点击提示标出实际操作位置。
    cueSeq = playSpriteGesture({ kind: 'point', target: targetCenter })
    await Promise.race([sleep(800), stage.hidden])

    // 指向期间被打断（拖拽开始、收起或隐藏会撤下提示）或舞台已不可见时同样跳过后续仪式；
    // 此后到预点击之间没有等待，预点击时舞台仍可见。
    if ($spriteGesture.get()?.seq !== cueSeq || !isActionStageVisible()) {
      return await execute()
    }

    cueSeq = playSpriteGesture({ kind: 'tap', target: targetCenter })
    setSpriteState('interacting', { durationMs: 1500 })

    // 预点击只对「点击不是工具本体」的仪式有意义（open_application 聚焦已开窗口）。
    // click_at 工具本身就是要执行的那次点击——再补一次就是双击。
    if (opts?.previewClick !== false && window.spiritagent?.runnerInvoke) {
      window.spiritagent
        .runnerInvoke('system.click_at', { x: Math.round(geom.x + geom.w / 2), y: Math.round(geom.y + geom.h / 2) })
        .catch(error => {
          log.warn('ritual-walk', 'Preview click failed', error)
        })
    }

    await sleep(400)

    return await execute()
  } finally {
    stage.stop()

    if (cueSeq !== null) {
      clearSpriteGesture(cueSeq)
    }

    // 可见时在目标旁稍作停留再交回空间决策；不可见时空间决策已暂停，不再等待。
    if (isActionStageVisible()) {
      await sleep(800)
    }

    updateSpatialDecision()
  }
}
