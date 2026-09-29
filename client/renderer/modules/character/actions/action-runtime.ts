/** 动作播放运行时：播放实例、抢占、回执。
 *
 * 不把每个新动作加入基础状态机；基础状态（idle/drag/walk）仍是表现优先级真源，
 * 本模块只管理数据化表达请求（play_id + epoch + TTL），由 VideoStage 消费。
 * 相同素材路径不跳过切换：同一动作再次播放使用新 play_id 从头播放。
 */

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'

import { endTransientState, setSpriteState } from '../companion-store'

import type { ActionPlaybackStatus, ActionPlayCommand, ActionPlayInstance } from './action-types'

/** 当前生效播放实例；null 表示无表达请求，回退基础状态机。 */
export const $activePlayInstance = atom<ActionPlayInstance | null>(null)

/** 播放代次：新请求或抢占递增；旧 ended/error/timeout 回调凭它不终止新动作。 */
let playGeneration = 0

/** 已受理 play_id：同一指令重复到达不重建实例、不从头重播。 */
const acceptedPlayIds = new Set<string>()
const MAX_TRACKED_PLAY_IDS = 64
const settledInstances = new WeakSet<ActionPlayInstance>()
// 视频事件可能略晚于标称时长：情绪态以收尾为准，计时只作兜底。
const EXPRESSION_STATE_GRACE_MS = 1000
/** 当前以情绪态呈现的表达实例；只有它的收尾能结束情绪态。 */
let expressionOwner: ActionPlayInstance | null = null

function endExpressionState(): void {
  if (expressionOwner !== null) {
    expressionOwner = null
    endTransientState('emotional')
  }
}

function rememberPlayId(playId: string): void {
  acceptedPlayIds.add(playId)

  if (acceptedPlayIds.size > MAX_TRACKED_PLAY_IDS) {
    const oldest = acceptedPlayIds.values().next().value

    if (oldest !== undefined) {
      acceptedPlayIds.delete(oldest)
    }
  }
}

function isExpired(expiresAtMs: number | null): boolean {
  return expiresAtMs !== null && Date.now() > expiresAtMs
}

/** 换包、外观代次变化或账号清理时作废旧实例，异步媒体回调继续核对当前播放代次。 */
export function resetActionPlayback(): void {
  endExpressionState()
  $activePlayInstance.set(null)
  acceptedPlayIds.clear()
}

/** 受理已由主进程认领的播放指令：校验包与外观代次 / 素材 / TTL / play_id 去重，生成播放实例。
 * 认领后请求不再交给其他舞台，不能受理时上报 rejected，避免账本停留在 queued。
 * 拖拽等更高优先级交互由调度器在调用前裁决，本函数不做交互判断。 */
export function acceptPlayCommand(
  command: ActionPlayCommand,
  clip: ActionPlayInstance['clip'] | null,
  rendered: { readonly packId: number; readonly appearanceEpoch: number | null }
): ActionPlayInstance | null {
  // 外观代次随每次激活递增：他包或旧代次（含换装后再穿回同一包）的请求不在当前外观执行。
  if (command.pack_id !== rendered.packId || command.appearance_epoch !== rendered.appearanceEpoch) {
    void reportReceipt(command, 'rejected', 'appearance changed')

    return null
  }

  // 同代次目录已移除该动作或推进了素材版本。
  if (clip === null) {
    void reportReceipt(command, 'rejected', 'clip unavailable')

    return null
  }

  const expiresAtMs = command.expires_at !== null ? Date.parse(command.expires_at) : null

  // TTL：过期请求不补播。
  if ((expiresAtMs !== null && !Number.isFinite(expiresAtMs)) || isExpired(expiresAtMs)) {
    void reportReceipt(command, 'rejected', 'expired')

    return null
  }

  // 同一 play_id 只受理一次，避免重复指令从头重播。
  if (acceptedPlayIds.has(command.play_id)) {
    return $activePlayInstance.get()
  }

  playGeneration += 1

  const instance: ActionPlayInstance = {
    playId: command.play_id,
    actionId: command.action_id,
    assetRevisionId: command.asset_revision_id,
    clip,
    repeatCount: Math.max(1, Math.min(5, command.repeat_count)),
    expiresAtMs,
    generation: playGeneration
  }

  rememberPlayId(command.play_id)
  $activePlayInstance.set(instance)

  return instance
}

/** 实际开播前再次检查 TTL：拖拽期间排队的过期动作不播。 */
export function shouldStartInstance(instance: ActionPlayInstance): boolean {
  if ($activePlayInstance.get()?.generation !== instance.generation) {
    return false
  }

  if (isExpired(instance.expiresAtMs)) {
    settlePlayInstance(instance, 'rejected', 'expired')

    return false
  }

  return true
}

/** 表达真实可见：上报 started，并在播放期间处于情绪态（DESIGN：由动作承载情绪）。 */
export function markPlayInstanceStarted(instance: ActionPlayInstance): void {
  void reportReceipt({ play_id: instance.playId }, 'started')
  expressionOwner = instance
  const plays = instance.clip.loopable ? instance.repeatCount : 1
  setSpriteState('emotional', { durationMs: instance.clip.duration_ms * plays + EXPRESSION_STATE_GRACE_MS })
}

/** 上报播放回执；按 play_id 幂等，服务端聚合使用量。 */
export async function reportReceipt(
  command: Pick<ActionPlayCommand, 'play_id'>,
  status: ActionPlaybackStatus,
  reason: string = '',
  visibleDurationMs: number = 0
): Promise<void> {
  const result = await authedApi({
    body: {
      play_id: command.play_id,
      status,
      visible_duration_ms: visibleDurationMs,
      error: reason || null
    },
    method: 'POST',
    path: `/api/companion/actions/playback/${command.play_id}/receipt`
  })

  if (!result.ok && result.reason === 'err') {
    log.warn('action-runtime', 'receipt report failed', result.error)
  }
}

/** 实例仅收尾一次；React 清理与迟到媒体事件不能给同一动作报告第二种终态。 */
export function settlePlayInstance(
  instance: ActionPlayInstance,
  status: Exclude<ActionPlaybackStatus, 'started'>,
  reason = '',
  visibleDurationMs = 0
): void {
  if (settledInstances.has(instance)) {
    return
  }

  settledInstances.add(instance)
  void reportReceipt({ play_id: instance.playId }, status, reason, visibleDurationMs)

  if (expressionOwner === instance) {
    endExpressionState()
  }

  if ($activePlayInstance.get()?.generation === instance.generation) {
    $activePlayInstance.set(null)
  }
}
