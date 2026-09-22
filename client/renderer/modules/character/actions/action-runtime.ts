/** 动作播放运行时：播放实例、抢占、回执。
 *
 * 不把每个新动作加入基础状态机；基础状态（idle/drag/walk）仍是表现优先级真源，
 * 本模块只管理数据化表达请求（play_id + epoch + TTL），由 VideoStage 消费。
 * 相同素材路径不跳过切换：同一动作再次播放使用新 play_id 从头播放。
 */

import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'

import type { ActionPlaybackStatus, ActionPlayCommand, ActionPlayInstance } from './action-types'

/** 当前生效播放实例；null 表示无表达请求，回退基础状态机。 */
export const $activePlayInstance = atom<ActionPlayInstance | null>(null)

/** 外观世代：每次目录水合 / 外观切换递增；旧实例凭它被拒（A→B→A 后第一次 A 的迟到指令不播放）。 */
let appearanceEpochCounter = 0

/** 播放代次：新请求或抢占递增；旧 ended/error/timeout 回调凭它不终止新动作。 */
let playGeneration = 0

/** 已受理 play_id：同一指令重复到达不重建实例、不从头重播。 */
const acceptedPlayIds = new Set<string>()
const MAX_TRACKED_PLAY_IDS = 64

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

export function nextAppearanceEpoch(): number {
  appearanceEpochCounter += 1

  // 换装 / 目录水合后旧实例作废，避免沿用旧 play_id 与旧素材上报。
  if ($activePlayInstance.get() !== null) {
    $activePlayInstance.set(null)
  }

  acceptedPlayIds.clear()

  return appearanceEpochCounter
}

export function currentAppearanceEpoch(): number {
  return appearanceEpochCounter
}

/** 受理播放指令：校验包归属 / TTL / play_id 去重，生成播放实例。
 * 拖拽等更高优先级交互由调度器在调用前裁决，本函数不做交互判断。
 * appearance_epoch 采用“客户端单调计数、服务端只透传目录版本”的宽松契约：
 * 指令 epoch 小于当前世代只说明产生于更早目录，仍允许播放（外观隔离由 pack_id 保证）。 */
export function acceptPlayCommand(
  command: ActionPlayCommand,
  clip: ActionPlayInstance['clip'] | null,
  renderedPackId: number
): ActionPlayInstance | null {
  if (clip === null) {
    return null
  }

  // 包不匹配：B 包画面不执行 A 包指令。
  if (command.pack_id !== renderedPackId) {
    void reportReceipt(command, 'rejected', 'pack mismatch')

    return null
  }

  const expiresAtMs = command.expires_at !== null ? Date.parse(command.expires_at) : null

  // TTL：过期请求不补播。
  if (isExpired(expiresAtMs)) {
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
    packId: command.pack_id,
    appearanceEpoch: command.appearance_epoch,
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
    void reportReceipt({ play_id: instance.playId }, 'rejected', 'expired')
    finishPlayInstance(instance.generation)

    return false
  }

  return true
}

/** 上报播放回执；按 play_id 幂等，服务端聚合使用量。 */
export async function reportReceipt(
  command: Pick<ActionPlayCommand, 'play_id'>,
  status: ActionPlaybackStatus,
  reason: string = '',
  visibleDurationMs: number = 0
): Promise<void> {
  try {
    const { authedApi } = await import('@/shared/lib/authed-api')
    await authedApi({
      body: {
        play_id: command.play_id,
        status,
        visible_duration_ms: visibleDurationMs,
        error: reason || null
      },
      method: 'POST',
      path: `/api/companion/actions/playback/${command.play_id}/receipt`
    })
  } catch (err) {
    log.warn('action-runtime', 'receipt report failed', err)
  }
}

/** 结束当前表达：once 播完、循环次数耗尽或被抢占后调用；实例回到基础状态机。 */
export function finishPlayInstance(generation: number): void {
  if ($activePlayInstance.get()?.generation === generation) {
    $activePlayInstance.set(null)
  }
}

/** 当前实例是否仍有效（未被更新请求替换）。 */
export function isInstanceCurrent(instance: ActionPlayInstance): boolean {
  return $activePlayInstance.get()?.generation === instance.generation
}
