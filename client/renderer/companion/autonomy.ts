import { clamp } from '@runtime'

import { $gateway } from '@/shared/store/gateway'
import { $runnerPhase } from '@/shared/store/runner-status'
import { $surfaceOpen } from '@/shared/store/surfaces'

import { $focusContext, $lastIdleSeconds, $screenLocked } from './activity'
import { $effectiveTier, lockGazeToPoint } from './companion-store'
import { $llmAutonomy } from './prefs'
import { gazeTowardsPoint } from './ritual-walk'
import {
  $defaultScale,
  $spatialPos,
  computePerchPlacement,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  moveTo,
  setLocale,
  startRoam
} from './spatial'

const CONSULT_MIN_INTERVAL_MS = 60_000
const MIN_ACTION_QUIET_MS = 60_000
const BACKGROUND_CONSULT_INTERVAL_MS = 30 * 60_000

interface Snapshot {
  focused_category: string
  fullscreen: boolean
  locked: boolean
}

interface ShouldActRpcResponse {
  should_act?: boolean
  action?: string
  params?: Record<string, unknown>
  reason?: string
}

let lastConsultAt = 0
let lastAutonomousActionAt = 0
let lastSnapshot: Snapshot | null = null
let backgroundTimer: ReturnType<typeof setInterval> | null = null
let unsubs: Array<() => void> = []
let active = false

function stateChanged(oldSnap: Snapshot, newSnap: Snapshot): boolean {
  return (
    oldSnap.focused_category !== newSnap.focused_category ||
    oldSnap.fullscreen !== newSnap.fullscreen ||
    oldSnap.locked !== newSnap.locked
  )
}

// 走过去搭话的远近距离分界（DESIGN §3.6 同款语义：远飞近走）。
const APPROACH_WALK_RANGE_PX = 400

function approachLocomotion(target: { x: number; y: number }): 'walk' | 'fly' {
  const cur = $spatialPos.get()

  return Math.hypot(target.x - cur.x, target.y - cur.y) > APPROACH_WALK_RANGE_PX ? 'fly' : 'walk'
}

// 走过去搭话（DESIGN §3.5/§6.4）：开场白由后端经 companion.message 通道投递（边走边说），
// 客户端只负责走位——有焦点窗口落在窗口旁（复用 perch 落位与缩身，搭话后就地陪工）；
// 用户在桌面（无窗口）时走到屏幕中下部站定，不动 locale，后续空间决策自然接管。
// 途中视线锁定目标中心，数秒后交还指针跟随（镜像 events.ts 的 perch cue 模式）。
function executeApproach(): void {
  // 锁屏不搭话；聊天开着时空间决策本就冻结。
  if ($screenLocked.get() || $surfaceOpen.get() !== null) {
    return
  }

  const ctx = $focusContext.get()
  const geom = ctx?.windowGeom
  const hasWindow = Boolean(geom && ctx!.category !== 'unknown' && !ctx!.fullscreen)

  if (hasWindow && geom) {
    const perch = computePerchPlacement(geom, $defaultScale.get())

    if (perch) {
      lockGazeToPoint(gazeTowardsPoint({ x: geom.x + geom.w / 2, y: geom.y + geom.h / 2 }))
      setLocale('perch', {
        position: perch.pos,
        scaleLimit: perch.scale,
        locomotion: approachLocomotion(perch.pos)
      })
    }

    return
  }

  // 屏幕中下部居中站定（落在 roam waypoint 的下半屏带内，避免挡住用户正在用的 UI）。
  const vw = window.innerWidth
  const vh = window.innerHeight
  const w = getBaseSpriteWidth()
  const h = getBaseSpriteHeight()

  const point = {
    x: Math.max(24, (vw - w) / 2),
    y: clamp(vh * 0.6, 24, vh - h - 24)
  }

  lockGazeToPoint(gazeTowardsPoint(point))
  moveTo(point, approachLocomotion(point))
}

function executeAutonomousAction(action: string): void {
  switch (action) {
    case 'roam':
      startRoam()

      break
    case 'perch': {
      const ctx = $focusContext.get()

      if (ctx?.windowGeom && ctx.category !== 'unknown' && !ctx.fullscreen) {
        const perch = computePerchPlacement(ctx.windowGeom, $defaultScale.get())

        if (perch) {
          setLocale('perch', { position: perch.pos, scaleLimit: perch.scale })
        }
      }

      break
    }

    case 'approach':
      executeApproach()

      break

    default:
      break
  }
}

async function consultAutonomyLLM(force = false): Promise<void> {
  // 空间智能只服务当前可见的桌面精灵；生活空间等表面打开时精灵已收起，不发起推理。
  if (
    !$llmAutonomy.get() ||
    $effectiveTier.get() !== 'autonomous' ||
    $surfaceOpen.get() !== null ||
    $screenLocked.get()
  ) {
    return
  }

  const now = Date.now()

  if (now - lastConsultAt < CONSULT_MIN_INTERVAL_MS) {
    return
  }

  if (now - lastAutonomousActionAt < MIN_ACTION_QUIET_MS) {
    return
  }

  const idle = Math.max(0, $lastIdleSeconds.get())
  const focus = $focusContext.get()
  const locked = $screenLocked.get()
  const hour = new Date().getHours()

  const newSnapshot: Snapshot = {
    focused_category: focus?.category ?? 'unknown',
    fullscreen: focus?.fullscreen ?? false,
    locked
  }

  if (!force && lastSnapshot !== null && !stateChanged(lastSnapshot, newSnapshot)) {
    return
  }

  const gateway = $gateway.get()

  if (!gateway) {
    return
  }

  lastConsultAt = now
  lastSnapshot = newSnapshot

  const secondsSinceLastAction = lastAutonomousActionAt > 0 ? (now - lastAutonomousActionAt) / 1000 : 9999

  try {
    const res = await gateway.request<ShouldActRpcResponse>('companion.should_act', {
      kind: 'periodic_provision',
      idle_seconds: idle,
      local_hour: hour,
      focused_category: focus?.category ?? null,
      fullscreen: focus?.fullscreen ?? false,
      screen_locked: locked,
      seconds_since_last_action: secondsSinceLastAction
    })

    if (res?.should_act && res.action) {
      lastAutonomousActionAt = Date.now()
      executeAutonomousAction(res.action)
    }
  } catch {
    /* 静默捕获；LLM 错误时不做任何自主动作 */
  }
}

export function startAutonomyProvision(): () => void {
  if (active) {
    return stopAutonomyProvision
  }

  active = true

  const onStateOrEventChange = () => {
    if (active && $runnerPhase.get() === 'running') {
      void consultAutonomyLLM(false)
    }
  }

  unsubs.push($focusContext.subscribe(onStateOrEventChange))
  unsubs.push(
    $screenLocked.subscribe(locked => {
      if (locked) {
        lastSnapshot = null

        return
      }

      onStateOrEventChange()
    })
  )
  unsubs.push($lastIdleSeconds.subscribe(onStateOrEventChange))
  unsubs.push(
    $surfaceOpen.subscribe(surface => {
      if (surface !== null) {
        lastSnapshot = null

        return
      }

      onStateOrEventChange()
    })
  )
  unsubs.push(
    $llmAutonomy.subscribe(enabled => {
      if (!enabled) {
        lastSnapshot = null
      } else {
        onStateOrEventChange()
      }
    })
  )

  backgroundTimer = setInterval(() => {
    if (active && $runnerPhase.get() === 'running') {
      void consultAutonomyLLM(true)
    }
  }, BACKGROUND_CONSULT_INTERVAL_MS)

  // 显式触发首次咨询，而不是依赖任一 atom 的订阅回放语义。
  if ($runnerPhase.get() === 'running') {
    void consultAutonomyLLM(true)
  }

  return stopAutonomyProvision
}

export function stopAutonomyProvision(): void {
  active = false

  if (backgroundTimer !== null) {
    clearInterval(backgroundTimer)
    backgroundTimer = null
  }

  for (const unsub of unsubs) {
    unsub()
  }

  unsubs = []
  lastSnapshot = null
}
