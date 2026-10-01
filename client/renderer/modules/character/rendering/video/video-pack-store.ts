/** 视频包生成流程 store：衣柜页发起生成、进度状态与包列表；可播清单与播放实例由 actions 维护；渲染层不得经本 store 触发付费（缺失动作由显式服务流程统一鉴权、去重、记账）。 */

import { atom } from 'nanostores'

import { apiSucceeded, authedApi } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import { getStrings } from '@/shared/strings'

import type { VideoPackWire } from '../../actions'

export const $videoPacks = atom<VideoPackWire[]>([])

/** 生成/失败归属：按着装与动作包隔离展示，避免 A 的错误串到 B。 */
export interface VideoGenScope {
  outfitId: number | null
  packId: number | null
}

export interface VideoGenError extends VideoGenScope {
  message: string
}

// 生成状态机：事件驱动（progress/failed）优先，hydrate 用包列表兜底识别 processing 包；failed 携带后端公开文案可重试。
export const $videoGenState = atom<'idle' | 'generating' | 'failed'>('idle')
export const $videoGenStage = atom<VideoGenStage | null>(null)
export const $videoGenError = atom<VideoGenError | null>(null)
/** 当前生成/最近失败的归属；与错误一起决定界面是否展示。 */
export const $videoGenScope = atom<VideoGenScope | null>(null)

const VIDEO_GEN_STAGES = ['script', 'pose', 'submit', 'generate', 'download', 'process', 'publish'] as const

/** 按参考生成的任务阶段（对应后端 companion.video.progress 的 stage） */
export type VideoGenStage = (typeof VIDEO_GEN_STAGES)[number]

let inflight: Promise<boolean> | null = null
let generationRevision = 0
let requestingGeneration = false

/** 事件优先于旧请求响应；终态事件要求在已有水合之后重新读取。 */
export function videoPackEventReceived(): void {
  generationRevision += 1
}

/** 当前视图是否命中该归属；归属缺少着装时必须凭 packId 精确匹配。 */
export function videoGenScopeMatches(
  scope: VideoGenScope | null,
  outfitId: number | null,
  packId: number | null
): boolean {
  if (!scope) {
    return false
  }

  if (scope.outfitId == null) {
    return scope.packId != null && scope.packId === packId
  }

  if (scope.outfitId !== outfitId) {
    return false
  }

  if (scope.packId != null && packId != null && scope.packId !== packId) {
    return false
  }

  return true
}

function resolveGenScope(
  opts: { outfitId?: number | null; sourcePackId?: number; retryPackId?: number },
  packId?: number | null
): VideoGenScope {
  const resolvedPackId = packId ?? opts.sourcePackId ?? opts.retryPackId ?? null
  const pack = resolvedPackId != null ? $videoPacks.get().find(p => p.id === resolvedPackId) : null

  return {
    outfitId: opts.outfitId ?? pack?.outfit_id ?? null,
    packId: resolvedPackId
  }
}

function setGenFailed(message: string, scope: VideoGenScope): void {
  $videoGenState.set('failed')
  $videoGenStage.set(null)
  $videoGenScope.set(scope)
  $videoGenError.set({ message, ...scope })
}

function setGenIssue(message: string, scope: VideoGenScope): void {
  $videoGenError.set({ message, ...scope })
}

function clearGenIssue(): void {
  $videoGenError.set(null)
}

function eventScope(scope: Partial<VideoGenScope>): VideoGenScope {
  return { outfitId: scope.outfitId ?? null, packId: scope.packId ?? null }
}

/** 视频包就绪 / 激活事件：生成态收敛为空闲，归属取事件载荷。 */
export function videoGenReady(scope: Partial<VideoGenScope>): void {
  videoPackEventReceived()
  $videoGenState.set('idle')
  $videoGenStage.set(null)
  clearGenIssue()
  $videoGenScope.set(eventScope(scope))
}

/** 生成阶段事件：未知阶段按无阶段处理；事件缺少着装时依次取包列表、同一动作包上次的归属。 */
export function videoGenProgress(stage: string | undefined, scope: Partial<VideoGenScope>): void {
  const { outfitId, packId } = resolveGenScope({ outfitId: scope.outfitId }, scope.packId)
  const previous = $videoGenScope.get()

  videoPackEventReceived()
  $videoGenState.set('generating')
  $videoGenStage.set(VIDEO_GEN_STAGES.find(known => known === stage) ?? null)
  clearGenIssue()
  $videoGenScope.set({
    outfitId: outfitId ?? (packId !== null && previous?.packId === packId ? previous.outfitId : null),
    packId
  })
}

/** 生成失败事件：文案取后端公开原因，缺省用通用提示。 */
export function videoGenFailed(reason: string | undefined, scope: Partial<VideoGenScope>): void {
  videoPackEventReceived()
  setGenFailed(reason || getStrings().living.appearance.videoGenRequestFailed, eventScope(scope))
}

registerStorageClearHandler(() => {
  inflight = null
  generationRevision += 1
  requestingGeneration = false
  $videoPacks.set([])
  $videoGenState.set('idle')
  $videoGenStage.set(null)
  $videoGenError.set(null)
  $videoGenScope.set(null)
})

/** 刷新包列表与生成状态；事件丢失或离线期间的兜底。返回是否取得当前列表，失败原因记日志。 */
export async function hydrateVideoPack(refresh = false): Promise<boolean> {
  if ($auth.get().kind !== 'authenticated') {
    return false
  }

  if (inflight) {
    if (refresh) {
      const epoch = currentClearEpoch()
      await inflight

      return epoch === currentClearEpoch() ? hydrateVideoPack() : false
    }

    return inflight
  }

  const epoch = currentClearEpoch()
  const revision = generationRevision

  const read = (async (): Promise<boolean> => {
    try {
      const res = await authedApi<{ packs?: VideoPackWire[] }>({ path: '/api/companion/video-packs' })

      if (epoch !== currentClearEpoch() || revision !== generationRevision) {
        return false
      }

      if (!apiSucceeded(res, 'video-pack-store', 'hydrateVideoPack failed')) {
        return false
      }

      if (!res.value) {
        return false
      }

      const packs = res.value.packs ?? []
      $videoPacks.set(packs)
      const scope = $videoGenScope.get()

      const matchesScope = (p: VideoPackWire): boolean => videoGenScopeMatches(scope, p.outfit_id, p.id)

      const processing =
        packs.find(p => p.status === 'processing' && matchesScope(p)) ?? packs.find(p => p.status === 'processing')

      if (processing) {
        $videoGenState.set('generating')
        $videoGenScope.set({ outfitId: processing.outfit_id, packId: processing.id })
        clearGenIssue()
      } else if ($videoGenState.get() === 'generating') {
        // 服务端已无进行中任务（进程重启按失败落库）时本地生成态收敛；失败文案以 companion.video.failed 事件为准。
        const failed =
          packs.find(p => p.status === 'failed' && matchesScope(p)) ?? packs.find(p => p.status === 'failed') ?? null

        if (failed) {
          setGenFailed(failed.error || getStrings().living.appearance.videoGenRequestFailed, {
            outfitId: failed.outfit_id,
            packId: failed.id
          })
        } else {
          $videoGenState.set('idle')
          $videoGenStage.set(null)
          clearGenIssue()
        }
      }

      return true
    } catch (err) {
      log.warn('video-pack-store', 'hydrateVideoPack failed', err)

      return false
    }
  })()

  // 事件先于响应到达时本次结果已丢弃，以重新读取的结果为准。
  const load = read.then(loaded => {
    if (epoch !== currentClearEpoch()) {
      return false
    }

    inflight = null

    return revision === generationRevision ? loaded : hydrateVideoPack()
  })

  inflight = load

  return load
}

/** 发起按参考生成（LLM 演绎脚本 → i2v → 服务端处理），进度与结果经 companion.video 事件回流；请求被拒绝时把后端公开文案写入失败态，不进入 generating。 */
export async function generateVideoPack(
  opts: {
    force?: boolean
    outfitId?: number
    sourcePackId?: number
    action?: string
    feedback?: string
    retryPackId?: number
  } = {}
): Promise<boolean> {
  if ($auth.get().kind !== 'authenticated') {
    return false
  }

  if (requestingGeneration || $videoGenState.get() === 'generating') {
    // 仅记录本次被拒请求的归属，不把进行中的任务改写成 failed。
    setGenIssue(getStrings().living.appearance.videoGenBusy, resolveGenScope(opts))

    return false
  }

  const epoch = currentClearEpoch()
  const revision = generationRevision
  requestingGeneration = true

  const res = await authedApi<VideoPackWire>({
    body: {
      force: opts.force === true,
      outfit_id: opts.outfitId,
      source_pack_id: opts.sourcePackId,
      action: opts.action,
      feedback: opts.feedback
    },
    method: 'POST',
    path: opts.retryPackId
      ? `/api/companion/video-packs/${opts.retryPackId}/retry`
      : '/api/companion/video-packs/generate'
  })

  if (epoch !== currentClearEpoch()) {
    return false
  }

  requestingGeneration = false

  if (revision !== generationRevision) {
    return res.ok
  }

  if (!res.ok) {
    if (res.reason === 'err') {
      setGenFailed(
        backendDetailMessage(res.error, getStrings().living.appearance.videoGenRequestFailed),
        resolveGenScope(opts)
      )
    }

    return false
  }

  if (!res.value) {
    return false
  }

  const scope = resolveGenScope(opts, res.value.id)
  $videoGenState.set(res.value.status === 'processing' ? 'generating' : 'idle')
  $videoGenStage.set(res.value.status === 'processing' ? 'script' : null)
  $videoGenScope.set(scope)
  clearGenIssue()

  await hydrateVideoPack(true)

  return true
}

export async function activateVideoPack(packId: number): Promise<boolean> {
  const epoch = currentClearEpoch()
  const res = await authedApi({ method: 'PUT', path: `/api/companion/video-packs/${packId}/activate` })

  if (epoch !== currentClearEpoch()) {
    return false
  }

  if (!res.ok) {
    if (res.reason === 'err') {
      // 穿着失败也要进入失败态，界面才能标红并展示原因。
      setGenFailed(
        backendDetailMessage(res.error, getStrings().living.appearance.videoActivateFailed),
        resolveGenScope({}, packId)
      )
    }

    return false
  }

  $videoGenState.set('idle')
  $videoGenStage.set(null)
  $videoGenScope.set(null)
  clearGenIssue()
  await hydrateVideoPack(true)

  return true
}
