import type { StageActivity } from '@ipc/contracts'
import type { DesktopScreenRect } from '@ipc/contracts'
import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'
import { $chatVisible } from '@/shared/store/chat-visibility'
import { $gateway } from '@/shared/store/gateway'
import { $presentation } from '@/shared/store/presentation'
import { $runnerPhase } from '@/shared/store/runner-status'
import { isCompanionStageVisible } from '@/shared/store/surfaces'

import {
  $effectiveTier,
  $effectiveTierOverride,
  $quietUntil,
  $userPreferredTier,
  type DisturbanceTier,
  pushEffectiveDisturbanceTier
} from './companion-store'
import { $llmAffect } from './prefs'

// 本地环境信号取自 Runner 的 system.* 工具，伙伴层直接据此推理（不经 LLM）；Runner 离线或探测失败时空闲时长回到 -1。

export const $screenLocked = atom<boolean>(false)
// -1 表示本周期无信号（Runner 离线或探测失败），调用方按未知处理。
export const $lastIdleSeconds = atom<number>(-1)

type FocusCategory = 'ide' | 'music' | 'reader' | 'gaming' | 'browsing' | 'other' | 'unknown'

interface FocusContext {
  category: FocusCategory
  fullscreen: boolean
  windowGeom?: DesktopScreenRect
  windowId?: string
  windowPid?: number
  runnerInstanceId?: string
}

export const $focusContext = atom<FocusContext | null>(null)

const POLL_INTERVAL_MS = 30_000
const IDLE_THRESHOLD_SECONDS = 30 * 60
const CHECK_COOLDOWN_MS = 60 * 60 * 1000
const STATS_POST_THROTTLE_MS = 60_000

let timer: ReturnType<typeof setInterval> | null = null
let lastIdleExpressionAt = 0
let lastTierPushed: DisturbanceTier | null = null
let runnerReady = false
let unsubs: Array<() => void> = []
let monitorGeneration = 0
let polling = false
let lastSignalContext: string | null = null
let pendingSignalContextChange = false
let signalRevision = 0

let localChatTurnCount = 0
let lastChatTurnSentAt = 0

function maybeTriggerIdleExpression(idleSeconds: number, locked: boolean): void {
  // 自主表演条件与播放共用可见性判断（见 renderer README），发请求时读取；播放指令由后端统一派发。
  if (
    !$llmAffect.get() ||
    $effectiveTier.get() !== 'autonomous' ||
    locked ||
    $chatVisible.get() ||
    !isCompanionStageVisible() ||
    idleSeconds < IDLE_THRESHOLD_SECONDS
  ) {
    return
  }

  const now = Date.now()

  if (now - lastIdleExpressionAt < CHECK_COOLDOWN_MS) {
    return
  }

  // 夜间政策权威在服务端，客户端只传 local_hour，不在此硬编码跳过。
  const hour = new Date().getHours()

  lastIdleExpressionAt = now
  const gateway = $gateway.get()
  void gateway
    ?.request('companion.idle_expression', {
      idle_seconds: idleSeconds,
      local_hour: hour
    })
    .catch(error => {
      log.warn('activity', 'companion.idle_expression failed', error)
    })
}

interface FocusedAppInfo {
  name?: string
  title?: string
  bundle?: string
  x?: number
  y?: number
  w?: number
  h?: number
  window_id?: string
}

type CategoryTable = Record<Exclude<FocusCategory, 'unknown' | 'other'>, readonly string[]>

const WINDOWS_ALLOWLIST = {
  ide: [
    'code.exe',
    'devenv.exe',
    'idea64.exe',
    'pycharm64.exe',
    'webstorm64.exe',
    'sublime_text.exe',
    'nvim.exe',
    'vim.exe',
    'clion.exe',
    'rider.exe',
    'rubymine64.exe',
    'goland64.exe',
    'atom.exe'
  ],
  music: ['spotify.exe', 'qqmusic.exe', 'cloudmusic.exe', 'musicbee.exe', 'foobar2000.exe'],
  reader: [
    'acrobat.exe',
    'acrord32.exe',
    'sumatrapdf.exe',
    'zathura.exe',
    'calibre.exe',
    'ebookreader.exe',
    'foxitreader.exe'
  ],
  gaming: [
    'steam.exe',
    'epicgameslauncher.exe',
    'minecraft.exe',
    'riotclientux.exe',
    'riotclientservices.exe',
    'battle.net.exe',
    'origin.exe',
    'steamwebhelper.exe'
  ],
  browsing: ['chrome.exe', 'firefox.exe', 'msedge.exe', 'brave.exe', 'opera.exe', 'vivaldi.exe']
} as const satisfies CategoryTable

const MACOS_BUNDLE_PREFIXES = {
  ide: [
    'com.microsoft.vscode',
    'com.jetbrains.',
    'com.sublimetext.',
    'com.qvacua.vim',
    'org.vim.macvim',
    'com.github.atom'
  ],
  music: ['com.spotify.client', 'com.netease.163music', 'com.apple.music'],
  reader: ['com.adobe.acrobat', 'com.adobe.reader', 'com.apple.ibooks', 'read.amazon.kindle'],
  gaming: ['com.valvesoftware.steam', 'com.epicgames.epicgameslauncher'],
  browsing: [
    'com.google.chrome',
    'org.mozilla.firefox',
    'com.microsoft.edgemac',
    'com.brave.browser',
    'com.operasoftware.opera'
  ]
} as const satisfies CategoryTable

function classifyWindows(info: FocusedAppInfo): FocusCategory {
  const name = (info.name ?? '').toLowerCase()

  for (const cat of ['ide', 'music', 'reader', 'gaming', 'browsing'] as const) {
    for (const token of WINDOWS_ALLOWLIST[cat]) {
      if (name === token || name.endsWith(`\\${token}`)) {
        return cat
      }
    }
  }

  return 'unknown'
}

function classifyMacos(info: FocusedAppInfo): FocusCategory {
  const bundle = (info.bundle ?? '').toLowerCase()
  const name = (info.name ?? '').toLowerCase()

  for (const cat of ['ide', 'music', 'reader', 'gaming', 'browsing'] as const) {
    for (const prefix of MACOS_BUNDLE_PREFIXES[cat]) {
      if (bundle.startsWith(prefix) || name.includes(prefix.replace('com.', ''))) {
        return cat
      }
    }
  }

  return 'unknown'
}

function classifyFocusedApp(info: FocusedAppInfo): FocusCategory {
  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

  return isMac ? classifyMacos(info) : classifyWindows(info)
}

// 「沉浸式 → 静止」只覆盖真正浸没型上下文（游戏 / 全屏）；IDE/阅读等专注工作不压档（专注≠不可打扰），游戏窗口化也按沉浸处理。
const IMMERSIVE_CATEGORIES: ReadonlySet<FocusCategory> = new Set(['gaming'])

// 活动覆盖只表达沉浸情境；手动静止与临时安静由 $effectiveTier 统一裁决，不在此重复推导。
function computeImmersiveOverride(ctx: FocusContext | null): DisturbanceTier | null {
  return ctx && (ctx.fullscreen || IMMERSIVE_CATEGORIES.has(ctx.category)) ? 'still' : null
}

function maybePushTierOverride(): void {
  $effectiveTierOverride.set(computeImmersiveOverride($focusContext.get()))

  // 推送统一裁决后的生效档位（含临时安静），仅按值去重：只有生效值变化时才推送。
  const effective = $effectiveTier.get()

  if (lastTierPushed === effective) {
    return
  }

  lastTierPushed = effective
  pushEffectiveDisturbanceTier(effective)
}

// Runner 活动快照聚合：一次 system.snapshot 取回四项信号，单项探针失败时返回与各独立工具相同的默认值。
interface SystemSnapshot {
  idle_seconds?: number
  locked?: boolean
  focused_app?: FocusedAppInfo | Record<string, never>
  fullscreen?: boolean
}

async function reportCompanionSignal(available: boolean, context: string | null = null): Promise<void> {
  const gateway = $gateway.get()
  const generation = monitorGeneration
  const revision = ++signalRevision

  if (context !== null && lastSignalContext !== null && context !== lastSignalContext) {
    pendingSignalContextChange = true
  }

  if (!gateway) {
    return
  }

  const event = available && pendingSignalContextChange ? 'context_changed' : undefined

  try {
    await gateway.request('companion.signal', { available, ...(event ? { event } : {}) })

    if (
      generation === monitorGeneration &&
      revision === signalRevision &&
      gateway === $gateway.get() &&
      available &&
      context !== null
    ) {
      lastSignalContext = context
      pendingSignalContextChange = false
    }
  } catch {
    // 保留未成功上报的变化；等待意图仍由后端到期扫描恢复。
  }
}

async function pollOnce(): Promise<void> {
  if (polling) {
    return
  }

  polling = true
  const generation = monitorGeneration

  try {
    await pollSnapshot(generation)
  } finally {
    if (generation === monitorGeneration) {
      polling = false
    }
  }
}

async function pollSnapshot(generation: number): Promise<void> {
  const desktop = window.spiritagent

  if (!desktop?.runnerInvoke) {
    return
  }

  const snapshotResult = await desktop.runnerInvoke('system.snapshot', {}).catch(() => null)

  if (generation !== monitorGeneration) {
    return
  }

  if (snapshotResult === null) {
    // 探测失败：空闲时长置未知，锁屏与焦点保留上次值；档位覆盖仍重算并上报当前生效档位。
    $lastIdleSeconds.set(-1)
    maybePushTierOverride()
    await reportCompanionSignal(false)

    return
  }

  const snapshot = snapshotResult as SystemSnapshot

  const windowScene = await desktop.sprite.getWindowScene().catch(() => null)

  if (generation !== monitorGeneration) {
    return
  }

  if (snapshot.locked !== undefined) {
    $screenLocked.set(Boolean(snapshot.locked))
  }

  const idleSeconds = Number(snapshot.idle_seconds ?? -1)

  // 非有限空闲值按缺失信号处理（NaN 会透传进后端 LLM prompt）。
  if (!Number.isFinite(idleSeconds)) {
    $lastIdleSeconds.set(-1)
    await reportCompanionSignal(false)

    return
  }

  // 缓存最近一次有限空闲值供其他模块读取，-1 表示本周期无信号。
  $lastIdleSeconds.set(idleSeconds)

  // 全屏位独立于聚焦分类跟踪，分类缺失时全屏仍压制主动表达。
  const fullscreenProbeOk = snapshot.fullscreen !== undefined

  const fullscreen = fullscreenProbeOk ? Boolean(snapshot.fullscreen) : ($focusContext.get()?.fullscreen ?? false)

  if (snapshot.focused_app && Object.keys(snapshot.focused_app).length > 0) {
    const focused = snapshot.focused_app as FocusedAppInfo
    const category = classifyFocusedApp(focused)

    const sceneWindow = windowScene?.windows.find(
      item => item.focused && (!focused.window_id || item.id === focused.window_id)
    )

    // 栖息只使用主进程统一转换后的视口坐标；探测失败不混入原生物理坐标。
    const windowGeom =
      sceneWindow && windowScene
        ? {
            x: sceneWindow.x - windowScene.viewport.x,
            y: sceneWindow.y - windowScene.viewport.y,
            w: sceneWindow.w,
            h: sceneWindow.h
          }
        : undefined

    const cur = $focusContext.get()

    const geomChanged =
      (windowGeom?.x ?? -1) !== (cur?.windowGeom?.x ?? -1) ||
      (windowGeom?.y ?? -1) !== (cur?.windowGeom?.y ?? -1) ||
      (windowGeom?.w ?? -1) !== (cur?.windowGeom?.w ?? -1) ||
      (windowGeom?.h ?? -1) !== (cur?.windowGeom?.h ?? -1) ||
      sceneWindow?.id !== cur?.windowId ||
      sceneWindow?.pid !== cur?.windowPid ||
      windowScene?.runnerInstanceId !== cur?.runnerInstanceId

    if (!cur || cur.category !== category || cur.fullscreen !== fullscreen || geomChanged) {
      $focusContext.set({
        category,
        fullscreen,
        runnerInstanceId: windowScene?.runnerInstanceId,
        windowGeom,
        windowId: sceneWindow?.id,
        windowPid: sceneWindow?.pid
      })
    }
  } else if (fullscreenProbeOk) {
    // focused-app 探测为空但 fullscreen 成功时保留分类，只更新 fullscreen 位。
    const cur = $focusContext.get()

    if (cur && cur.fullscreen !== fullscreen) {
      $focusContext.set({ ...cur, fullscreen })
    }
  }

  maybePushTierOverride()

  // 只上报可用性与变化类别，不上传窗口标题、应用名称或屏幕内容。
  const available =
    snapshot.locked === false &&
    fullscreenProbeOk &&
    !fullscreen &&
    idleSeconds >= 0 &&
    $effectiveTier.get() !== 'still'

  const context = `${$focusContext.get()?.category ?? 'unknown'}:${fullscreen}`

  await reportCompanionSignal(available, context)

  if (generation !== monitorGeneration) {
    return
  }

  maybeTriggerIdleExpression(snapshot.locked !== undefined ? idleSeconds : -1, $screenLocked.get())
}

export function applyStageActivity(activity: StageActivity): void {
  $screenLocked.set(activity.locked)
  $lastIdleSeconds.set(activity.idleSeconds)
  $focusContext.set(activity.focus)
  $effectiveTierOverride.set(activity.effectiveTier)
  maybeTriggerIdleExpression(activity.idleSeconds, activity.locked)
}

export function startActivityMonitor(): () => void {
  if (timer) {
    return stopActivityMonitor
  }

  monitorGeneration += 1

  const forwardStage = (): void => {
    if ($presentation.get().stageOwner !== 'desktop') {
      return
    }

    void window.spiritagent.presentation
      .stageActivity({
        locked: $screenLocked.get(),
        idleSeconds: $lastIdleSeconds.get(),
        effectiveTier: $effectiveTier.get(),
        focus: $focusContext.get()
      })
      .catch(error => log.warn('activity', 'Desktop activity forwarding failed', error))
  }

  unsubs.push(
    $presentation.listen(forwardStage),
    $screenLocked.listen(forwardStage),
    $lastIdleSeconds.listen(forwardStage),
    $focusContext.listen(forwardStage),
    $effectiveTier.listen(forwardStage)
  )
  unsubs.push(
    $effectiveTier.subscribe(tier => {
      if (tier === 'still') {
        void reportCompanionSignal(false)
      }
    })
  )

  // 偏好或临时安静变化（含其他窗口经 storage 同步）立即重算并推送，不等轮询，也不依赖 Runner 在线。
  unsubs.push($userPreferredTier.listen(() => maybePushTierOverride()))
  unsubs.push($quietUntil.listen(() => maybePushTierOverride()))

  let firstPollDone = false

  const kickFirstPoll = () => {
    if (firstPollDone) {
      return
    }

    firstPollDone = true
    void pollOnce()
  }

  // nanostore 订阅即触发一次回调，已 running 会立刻 kick 首次轮询；后续 running 只保持 runnerReady，一次性 latch 避免恢复时爆发轮询。
  unsubs.push(
    $runnerPhase.subscribe(phase => {
      if (phase === 'running') {
        runnerReady = true
        kickFirstPoll()
      } else if (phase === 'stopped' || phase === 'error') {
        // bridge 恢复后会再发 running；在此之前 setInterval tick 空操作，避免 IPC 错误日志刷屏。
        runnerReady = false
        monitorGeneration += 1
        polling = false
        $lastIdleSeconds.set(-1)
        void reportCompanionSignal(false)
      }
    })
  )

  timer = setInterval(() => {
    if (!runnerReady) {
      return
    }

    void pollOnce()
  }, POLL_INTERVAL_MS)

  return stopActivityMonitor
}

function stopActivityMonitor(): void {
  monitorGeneration += 1
  void reportCompanionSignal(false)
  polling = false
  $lastIdleSeconds.set(-1)
  lastSignalContext = null
  pendingSignalContextChange = false

  for (const unsub of unsubs) {
    unsub()
  }

  unsubs = []

  if (timer) {
    clearInterval(timer)
    timer = null
  }

  runnerReady = false
}

// 客户端 stats RPC 节流：前 10 次逐次发送让后端尽快越过当日阈值，之后每 60 秒至多一次，其间事件丢弃。
export function reportInteractionStat(kind: 'chat_turn'): void {
  const gateway = $gateway.get()

  if (!gateway) {
    return
  }

  localChatTurnCount += 1

  const now = Date.now()

  if (localChatTurnCount > 10 && now - lastChatTurnSentAt < STATS_POST_THROTTLE_MS) {
    return
  }

  lastChatTurnSentAt = now

  void gateway.request('companion.record_interaction_stats', { kind, hour: new Date().getHours() }).catch(() => {
    // 即发即忘
  })
}
