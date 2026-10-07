import { isRecord } from '@/shared/lib/is-record'
import { log } from '@/shared/lib/log'
import type { DesktopScreenRect } from '@ipc/contracts'
import { sleep } from '@runtime'

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
// 行走动画被中止（生活空间打开/拖拽取消移动且不回调）时的宽限：到点即视为行走结束，就地继续执行原工具。
const WALK_ABORT_GRACE_MS = 2000

// 仪式行走失败的离线/机械降级台词，走 speakProactive 档位门控（静止静默、常规仅气泡、自主开口）。
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

interface RunnerWindow extends DesktopScreenRect {
  name: string
  title: string
}

function isRunnerWindow(value: unknown): value is RunnerWindow {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.name === 'string' &&
    typeof value.title === 'string' &&
    Number.isFinite(value.x) &&
    Number.isFinite(value.y) &&
    Number.isFinite(value.w) &&
    Number.isFinite(value.h)
  )
}

export async function findWindowByKeyword(keyword: string): Promise<DesktopScreenRect | null> {
  // 空关键词会让 name.includes('') 恒真从而匹配到第一个窗口；关键词缺失 = 找不到目标。
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
async function toViewportRect(geom: DesktopScreenRect): Promise<DesktopScreenRect | null> {
  try {
    return await window.spiritagent.sprite.mapScreenRect(geom)
  } catch (error) {
    log.warn('ritual-walk', 'Could not map target into the sprite viewport', error)

    return null
  }
}

// 仪式只在精灵舞台实际可见时进行（与表达播放同一判断），每一步行动前重验；不可见时不走动、不出声、不预点击，直接执行原工具。
export async function performRitualWalk<T>(
  findTarget: () => Promise<DesktopScreenRect | null>,
  execute: () => Promise<T>,
  opts?: { previewClick?: boolean; signal?: AbortSignal; onPrepared?: () => void }
): Promise<T> {
  const available = (): boolean => isActionStageVisible() && !opts?.signal?.aborted

  if (!available()) {
    return execute()
  }

  let geom = await findTarget()

  for (let attempt = 0; !geom && attempt < RETRY_COUNT && available(); attempt++) {
    await sleep(RETRY_MS)
    geom = await findTarget()
  }

  const view = geom && available() ? await toViewportRect(geom) : null

  // 查找与换算期间舞台变为不可见：不再出声或走动，直接执行原工具。
  if (!available()) {
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
  let stopAbort: (() => void) | undefined

  const cancelled = new Promise<false>(resolve => {
    if (!opts?.signal) {
      return
    }

    const signal = opts.signal
    const abort = (): void => resolve(false)
    signal.addEventListener('abort', abort, { once: true })
    stopAbort = () => signal.removeEventListener('abort', abort)

    if (signal.aborted) {
      abort()
    }
  })

  const interrupted = Promise.race([stage.hidden, cancelled])

  try {
    const dist = Math.hypot(perch.x - $spatialPos.get().x, perch.y - $spatialPos.get().y)
    const locomotion = locomotionForDistance(dist)

    // 到达回调在行走被取消时不触发；仪式行走只是装饰，限时等待后必须继续执行原工具。
    const arrived = await Promise.race([
      new Promise<boolean>(resolve =>
        setSpatialLocale('perch', { position: perch, locomotion, onArrive: () => resolve(true) })
      ),
      sleep(moveDurationMs(dist, locomotion) + WALK_ABORT_GRACE_MS).then(() => false),
      interrupted
    ])

    // 行走未抵达（被拖拽、收起、隐藏或锁屏打断）时不再指向或预点击，直接执行原工具。
    if (!arrived) {
      return await execute()
    }

    // 抵达后指向目标，再以点击提示标出实际操作位置。
    cueSeq = playSpriteGesture({ kind: 'point', target: targetCenter })
    await Promise.race([sleep(800), interrupted])

    // 指向期间被打断（拖拽/收起/隐藏撤下提示）或舞台已不可见时跳过后续仪式；此后到预点击之间没有等待。
    if ($spriteGesture.get()?.seq !== cueSeq || !available()) {
      return await execute()
    }

    cueSeq = playSpriteGesture({ kind: 'tap', target: targetCenter })
    setSpriteState('interacting', { durationMs: 1500 })
    opts?.onPrepared?.()

    // 预点击只对「点击不是工具本体」的仪式有意义（open_application）；click_at 本身就是那次点击，再补一次就是双击。
    if (opts?.previewClick !== false && window.spiritagent?.runnerInvoke) {
      window.spiritagent
        .runnerInvoke('system.click_at', { x: Math.round(geom.x + geom.w / 2), y: Math.round(geom.y + geom.h / 2) })
        .catch(error => {
          log.warn('ritual-walk', 'Preview click failed', error)
        })
    }

    await Promise.race([sleep(400), interrupted])

    return await execute()
  } finally {
    stage.stop()
    stopAbort?.()

    if (cueSeq !== null) {
      clearSpriteGesture(cueSeq)
    }

    // 可见时在目标旁稍作停留再交回空间决策；不可见时空间决策已暂停，不再等待。
    if (available()) {
      await sleep(800)
    }

    if (!opts?.signal?.aborted) {
      updateSpatialDecision()
    }
  }
}
