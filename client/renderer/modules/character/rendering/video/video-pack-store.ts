/** 视频包生成流程 store：外观页发起生成、进度状态与包列表。
 * 可播清单与播放实例由 actions 模块维护；本 store 只管理生成任务状态机。
 * 渲染层不得经本 store 触发付费：缺失动作由显式服务流程统一鉴权、去重、记账。 */

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import { getStrings } from '@/shared/strings'

export interface VideoActionWire {
  action: string
  name: string
  kind: string
  status: string
  stage: string
  error: string | null
  clip_url: string | null
  motion_prompt: string
}

export interface VideoPackWire {
  id: number
  pack_version: number
  outfit_id: number | null
  error: string | null
  actions: VideoActionWire[]
  status: string
  active: boolean
  identity_review: 'none' | 'pass' | 'review' | 'accepted'
  manifest_url: string | null
  can_retry: boolean
  can_regenerate: boolean
}

export const $videoPacks = atom<VideoPackWire[]>([])

/** 生成/失败归属：按着装与动作包隔离展示，避免 A 的错误串到 B。 */
export interface VideoGenScope {
  outfitId: number | null
  packId: number | null
}

export interface VideoGenError extends VideoGenScope {
  message: string
}

// 生成状态机：事件驱动（progress/failed）优先，hydrate 用包列表兜底识别 processing 包；
// 登出清空。failed 携带后端公开文案，可从生成入口重试。
export const $videoGenState = atom<'idle' | 'generating' | 'failed'>('idle')
export const $videoGenStage = atom<VideoGenStage | null>(null)
export const $videoGenError = atom<VideoGenError | null>(null)
/** 当前生成/最近失败的归属；与错误一起决定界面是否展示。 */
export const $videoGenScope = atom<VideoGenScope | null>(null)

/** 按参考生成的任务阶段（对应后端 companion.video.progress 的 stage） */
export type VideoGenStage = 'script' | 'pose' | 'submit' | 'generate' | 'download' | 'process' | 'publish'

let inflight: Promise<void> | null = null
let generationRevision = 0
let requestingGeneration = false

/** 事件优先于旧请求响应；终态事件要求在已有水合之后重新读取。 */
export function videoPackEventReceived(): void {
  generationRevision += 1
}

/** 当前视图是否命中该归属；归属缺少着装时必须凭 packId 精确匹配。 */
export function videoGenScopeMatches(scope: VideoGenScope | null, outfitId: number, packId: number | null): boolean {
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
  opts: { outfitId?: number; sourcePackId?: number; retryPackId?: number },
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

/** 刷新包列表与生成状态；事件丢失或离线期间的兜底。 */
export async function hydrateVideoPack(refresh = false): Promise<void> {
  if ($auth.get().kind !== 'authenticated') {
    return
  }

  if (inflight) {
    if (refresh) {
      const epoch = currentClearEpoch()
      await inflight

      if (epoch === currentClearEpoch()) {
        await hydrateVideoPack()
      }

      return
    }

    return inflight
  }

  const epoch = currentClearEpoch()
  const revision = generationRevision

  const load = (async (): Promise<void> => {
    try {
      const res = await authedApi<{ packs?: VideoPackWire[] }>({ path: '/api/companion/video-packs' })

      if (epoch !== currentClearEpoch() || revision !== generationRevision) {
        return
      }

      if (!res.ok || !res.value) {
        return
      }

      const packs = res.value.packs ?? []
      $videoPacks.set(packs)
      const scope = $videoGenScope.get()

      const matchesScope = (p: VideoPackWire): boolean =>
        videoGenScopeMatches(scope, p.outfit_id ?? Number.MIN_SAFE_INTEGER, p.id)

      const processing =
        packs.find(p => p.status === 'processing' && matchesScope(p)) ?? packs.find(p => p.status === 'processing')

      if (processing) {
        $videoGenState.set('generating')
        $videoGenScope.set({ outfitId: processing.outfit_id, packId: processing.id })
        clearGenIssue()
      } else if ($videoGenState.get() === 'generating') {
        // 服务端已无进行中的任务（如处理进程重启按失败落库），本地生成态收敛；
        // 具体失败文案以 companion.video.failed 事件为准。
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
    } catch (err) {
      log.warn('video-pack-store', 'hydrateVideoPack failed', err)
    } finally {
      if (epoch === currentClearEpoch()) {
        inflight = null

        if (revision !== generationRevision) {
          void hydrateVideoPack()
        }
      }
    }
  })()

  inflight = load

  return load
}

/** 发起按参考生成（LLM 演绎脚本 → i2v → 服务端处理）；进度与结果经 companion.video 事件回流。
 * 请求被拒绝（守卫 / 供应商未配置）时把后端公开文案写入失败态，不进入 generating。 */
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
