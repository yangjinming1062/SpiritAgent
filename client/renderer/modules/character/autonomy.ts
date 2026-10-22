import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { $gateway } from '@/shared/store/gateway'
import { $presentation } from '@/shared/store/presentation'
import { $runnerPhase } from '@/shared/store/runner-status'
import type { PresentationMode } from '@ipc/contracts'
import { clamp } from '@runtime'

import { $focusContext, $lastIdleSeconds, $screenLocked } from './activity'
import { $effectiveTier } from './companion-store'
import {
  $defaultScale,
  $homePosition,
  $spatialLocomotion,
  $spatialPos,
  computePerchPlacement,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  locomotionForDistance,
  setSpatialLocale,
  startRoam
} from './spatial'
import { isCharacterStageVisible, observeCharacterStageVisibility } from './stage-visibility'

const CONSULT_MIN_INTERVAL_MS = 60_000
const MIN_ACTION_QUIET_MS = 60_000
const BACKGROUND_CONSULT_INTERVAL_MS = 30 * 60_000

interface Snapshot {
  presentation_mode: PresentationMode
  focused_category: string
  fullscreen: boolean
  locked: boolean
}

let lastConsultAt = 0
let lastAutonomousActionAt = 0
let lastSnapshot: Snapshot | null = null
let backgroundTimer: ReturnType<typeof setInterval> | null = null
let unsubs: Array<() => void> = []
let active = false
let provisionGeneration = 0
let consulting = false

// 精灵舞台须实际可见（精灵窗未隐藏或最小化、未被完整入口收起、未开轻语、未锁屏）；发请求前与结果返回后都按此重验。
function canConsultAutonomy(): boolean {
  return (
    active &&
    $runnerPhase.get() === 'running' &&
    $effectiveTier.get() === 'autonomous' &&
    isCharacterStageVisible() &&
    $spatialLocomotion.get() !== 'drag'
  )
}

// 作废在途咨询：迟到结果因代次变化被丢弃，条件恢复后重新判断。
function invalidateConsult(): void {
  provisionGeneration += 1
  lastSnapshot = null
}

function stateChanged(oldSnap: Snapshot, newSnap: Snapshot): boolean {
  return (
    oldSnap.presentation_mode !== newSnap.presentation_mode ||
    oldSnap.focused_category !== newSnap.focused_category ||
    oldSnap.fullscreen !== newSnap.fullscreen ||
    oldSnap.locked !== newSnap.locked
  )
}

function approachLocomotion(target: { x: number; y: number }): 'walk' | 'fly' {
  const cur = $spatialPos.get()

  return locomotionForDistance(Math.hypot(target.x - cur.x, target.y - cur.y))
}

// 走过去搭话（DESIGN「位置、移动与缩放」「自主动作与空间智能」）：开场白由后端经 companion.message 通道投递（边走边说），客户端只负责走位——有焦点窗口落在窗口旁（复用 perch 落位与缩身，搭话后就地陪工）；用户在桌面（无窗口）时走到屏幕中下部站定，后续空间决策自然接管。
function executeApproach(): void {
  const ctx = $focusContext.get()

  if (ctx?.windowGeom && ctx.category !== 'unknown' && !ctx.fullscreen) {
    const perch = computePerchPlacement(ctx.windowGeom, $defaultScale.get())

    if (perch) {
      setSpatialLocale('perch', {
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

  setSpatialLocale('target', { position: point, locomotion: approachLocomotion(point) })
}

// 调用方 isCurrent 已同步重验（含舞台可见），这里不重复判断。
function executeAutonomousAction(action: string): void {
  switch (action) {
    case 'roam':
      startRoam()

      break
    case 'perch': {
      executeApproach()

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
  // 空间智能只服务当前可见的窗口精灵；主窗口打开时精灵已收起，不发起推理。
  if (consulting || !canConsultAutonomy()) {
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
    presentation_mode: $presentation.get().effectiveMode,
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
  const generation = provisionGeneration
  const home = $homePosition.get()
  const stageEpoch = $presentation.get().stageEpoch

  const isCurrent = (): boolean =>
    generation === provisionGeneration &&
    gateway === $gateway.get() &&
    home === $homePosition.get() &&
    stageEpoch === $presentation.get().stageEpoch &&
    newSnapshot.presentation_mode === $presentation.get().effectiveMode &&
    !stateChanged(newSnapshot, {
      presentation_mode: $presentation.get().effectiveMode,
      focused_category: $focusContext.get()?.category ?? 'unknown',
      fullscreen: $focusContext.get()?.fullscreen ?? false,
      locked: $screenLocked.get()
    }) &&
    canConsultAutonomy()

  consulting = true

  try {
    if (!isCurrent()) {
      return
    }

    // 请求失败时不做任何自主动作。
    const res = await requestResource('companion.movement_decision', {
      body: {
        kind: 'periodic_provision',
        idle_seconds: idle,
        local_hour: hour,
        focused_category: focus?.category ?? null,
        fullscreen: focus?.fullscreen ?? false,
        screen_locked: locked,
        seconds_since_last_action: secondsSinceLastAction
      }
    }).catch(error => {
      log.warn('autonomy', 'companion.movement_decision failed', error)

      return null
    })

    if (isCurrent() && res?.should_act && res.action) {
      lastAutonomousActionAt = Date.now()
      executeAutonomousAction(res.action)
    }
  } finally {
    consulting = false
  }
}

export function startAutonomyProvision(): () => void {
  if (active) {
    return stopAutonomyProvision
  }

  active = true

  let presentationMode = $presentation.get().effectiveMode
  let stageEpoch = $presentation.get().stageEpoch
  unsubs.push(
    $presentation.listen(state => {
      if (state.effectiveMode !== presentationMode || state.stageEpoch !== stageEpoch) {
        presentationMode = state.effectiveMode
        stageEpoch = state.stageEpoch
        invalidateConsult()
      }
    })
  )

  const onStateOrEventChange = () => {
    void consultAutonomyLLM(false)
  }

  // subscribe 立即回放当前值，首次咨询由此发起；其余回放受最小咨询间隔节流。
  unsubs.push($focusContext.subscribe(onStateOrEventChange))
  // 完整入口、轻语、锁屏或精灵窗隐藏使舞台不可见：作废在途咨询，恢复可见后重新判断。
  unsubs.push(
    observeCharacterStageVisibility(visible => {
      if (!visible) {
        invalidateConsult()

        return
      }

      onStateOrEventChange()
    })
  )
  unsubs.push($lastIdleSeconds.subscribe(onStateOrEventChange))
  unsubs.push(
    $effectiveTier.subscribe(tier => {
      if (tier !== 'autonomous') {
        invalidateConsult()
      } else {
        onStateOrEventChange()
      }
    })
  )
  unsubs.push($runnerPhase.listen(invalidateConsult))

  backgroundTimer = setInterval(() => {
    void consultAutonomyLLM(true)
  }, BACKGROUND_CONSULT_INTERVAL_MS)

  return stopAutonomyProvision
}

export function stopAutonomyProvision(): void {
  active = false
  invalidateConsult()

  if (backgroundTimer !== null) {
    clearInterval(backgroundTimer)
    backgroundTimer = null
  }

  for (const unsub of unsubs) {
    unsub()
  }

  unsubs = []
}
