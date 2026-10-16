/** 动作包生成流程 store：衣柜页发起生成、进度状态与包列表；可用清单与播放实例由 actions 维护；渲染层不得经本 store 触发付费（缺失动作由显式服务流程统一鉴权、去重、记账）。
 * 着装及不同动作可并行生成；同一动作在结果确定前不重复提交。 */

import { atom } from 'nanostores'

import type { ActionPackWire } from '@/modules/character'
import { apiSucceeded } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { authedResource } from '@/shared/lib/resource-request'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import { getStrings } from '@/shared/strings'
import type { VideoQueueState } from '@protocol'

export const $videoPacks = atom<ActionPackWire[]>([])

/** 生成/失败归属：按着装、动作包与动作隔离展示。 */
export interface VideoGenScope {
  outfitId: number | null
  packId: number | null
  action: string | null
}

export interface VideoGenError extends VideoGenScope {
  message: string
  source: 'request' | 'generation'
}

// 请求、包、单动作与进度共同派生全局生成态；事件与水合统一收敛。
export const $videoGenState = atom<'idle' | 'generating' | 'failed'>('idle')
/** 按包隔离的生成阶段（键 = packId）；只在事件携带 packId 时写入。 */
export const $videoGenStages = atom<Record<number, VideoGenStage>>({})
/** 按归属隔离的错误（键 = scopeKey）；进度/就绪只清同 scope 的错误。 */
export const $videoGenErrors = atom<Record<string, VideoGenError>>({})
export const $videoGenRequests = atom<Record<string, VideoGenScope>>({})
/** 最近一次生成/失败的归属，用于补全同包进度缺失的着装信息。 */
export const $videoGenScope = atom<VideoGenScope | null>(null)

const VIDEO_GEN_STAGES = ['script', 'pose', 'submit', 'generate', 'download', 'process', 'publish'] as const

/** 按参考生成的任务阶段（对应后端 companion.video.progress 的 stage） */
export type VideoGenStage = (typeof VIDEO_GEN_STAGES)[number]

let inflight: Promise<boolean> | null = null
let generationRevision = 0
const scopeRevisions = new Map<string, number>()
const activeStages = new Map<number, Map<string | null, VideoGenStage>>()

function scopeKey(scope: VideoGenScope): string {
  if (scope.action != null) {
    const owner = scope.outfitId != null ? `outfit:${scope.outfitId}` : `pack:${scope.packId ?? 'unknown'}`

    return `${owner}:action:${scope.action}`
  }

  return scope.packId != null ? `pack:${scope.packId}` : `outfit:${scope.outfitId ?? 'unknown'}`
}

export function videoActionIsPending(status: string): boolean {
  return ['queued', 'running', 'processing', 'result_unknown'].includes(status)
}

/** 同着装同动作跨包去重；整包初建尚未创建目标行时也不能重复提交。 */
export function videoActionIsBusy(
  packs: ActionPackWire[],
  requests: Record<string, VideoGenScope>,
  outfitId: number | null,
  packId: number | null,
  action: string
): boolean {
  if (
    Object.values(requests).some(
      scope =>
        (scope.outfitId != null ? scope.outfitId === outfitId : scope.packId === packId) &&
        (scope.action == null || scope.action === action)
    )
  ) {
    return true
  }

  return packs.some(pack => {
    if (outfitId != null ? pack.outfit_id !== outfitId : pack.id !== packId) {
      return false
    }

    const entry = pack.actions.find(candidate => candidate.action === action)

    return entry ? videoActionIsPending(entry.status) || entry.can_regenerate === false : pack.status === 'processing'
  })
}

export function videoPackIsGenerating(pack: ActionPackWire): boolean {
  return (
    pack.status === 'processing' ||
    pack.actions.some(action => ['queued', 'running', 'processing'].includes(action.status))
  )
}

function refreshDerivedState(): void {
  const processing = $videoPacks.get().some(videoPackIsGenerating)

  if (processing || Object.keys($videoGenRequests.get()).length > 0 || Object.keys($videoGenStages.get()).length > 0) {
    $videoGenState.set('generating')

    return
  }

  const hasError = Object.keys($videoGenErrors.get()).length > 0
  $videoGenState.set(hasError ? 'failed' : 'idle')
}

function setScopedError(scope: VideoGenScope, message: string, source: VideoGenError['source'] = 'request'): void {
  $videoGenErrors.set({ ...$videoGenErrors.get(), [scopeKey(scope)]: { message, source, ...scope } })
  $videoGenScope.set(scope)
  refreshDerivedState()
}

function clearScopedErrors(scope: VideoGenScope, clearGroupActionErrors = false): void {
  const next = { ...$videoGenErrors.get() }
  const clearGroup = clearGroupActionErrors && scope.action == null
  let changed = false

  for (const [key, error] of Object.entries(next)) {
    if (!videoGenScopeMatches(error, scope.outfitId, scope.packId, clearGroup ? undefined : scope.action)) {
      continue
    }

    if (
      clearGroup &&
      error.action != null &&
      Object.values($videoGenRequests.get()).some(
        request =>
          videoGenScopeMatches(request, scope.outfitId, scope.packId) &&
          (request.action == null || request.action === error.action)
      )
    ) {
      continue
    }

    delete next[key]
    changed = true
  }

  if (changed) {
    $videoGenErrors.set(next)
  }
}

function setScopedStage(packId: number, stage: VideoGenStage | null, action: string | null = null): void {
  const stages = activeStages.get(packId) ?? new Map<string | null, VideoGenStage>()
  stages.delete(action)

  if (stage != null) {
    stages.set(action, stage)
  }

  if (stages.size > 0) {
    activeStages.set(packId, stages)
  } else {
    activeStages.delete(packId)
  }

  // 对外仍按包展示最近进度；一个动作结束不能清掉同包另一个动作的在途信号。
  const currentStage = [...stages.values()].at(-1) ?? null
  const next = { ...$videoGenStages.get() }

  if (currentStage == null) {
    if (packId in next) {
      delete next[packId]
      $videoGenStages.set(next)
    }

    return
  }

  if (next[packId] !== currentStage) {
    next[packId] = currentStage
    $videoGenStages.set(next)
  }
}

function clearStaleStages(): void {
  const packs = $videoPacks.get()
  const current = $videoGenStages.get()
  const packIds = new Set(packs.filter(videoPackIsGenerating).map(pack => pack.id))
  const next: Record<number, VideoGenStage> = {}

  for (const [packId, stage] of Object.entries(current)) {
    if (packIds.has(Number(packId))) {
      next[Number(packId)] = stage
    } else {
      activeStages.delete(Number(packId))
    }
  }

  $videoGenStages.set(next)
}

/** 事件优先于旧请求响应；终态事件要求在已有水合之后重新读取。 */
export function videoPackEventReceived(scope?: Partial<VideoGenScope>): void {
  generationRevision += 1

  if (scope) {
    const resolved = resolveGenScope({ outfitId: scope.outfitId, action: scope.action }, scope.packId)
    scopeRevisions.set(scopeKey(resolved), generationRevision)

    if (resolved.action != null && resolved.packId != null) {
      scopeRevisions.set(`pack:${resolved.packId}:action:${resolved.action}`, generationRevision)
    }

    if (resolved.outfitId != null && resolved.action == null) {
      scopeRevisions.set(`outfit:${resolved.outfitId}`, generationRevision)
    }
  }
}

function scopeRevision(scope: VideoGenScope): number {
  if (scope.action != null) {
    return Math.max(
      scopeRevisions.get(scopeKey(scope)) ?? 0,
      scopeRevisions.get(`pack:${scope.packId}:action:${scope.action}`) ?? 0
    )
  }

  return Math.max(
    scopeRevisions.get(scopeKey(scope)) ?? 0,
    scopeRevisions.get(`pack:${scope.packId}`) ?? 0,
    scopeRevisions.get(`outfit:${scope.outfitId}`) ?? 0
  )
}

/** 当前视图是否命中该归属；归属缺少着装时必须凭 packId 精确匹配。 */
export function videoGenScopeMatches(
  scope: VideoGenScope | null,
  outfitId: number | null,
  packId: number | null,
  action?: string | null
): boolean {
  if (!scope || (action !== undefined && scope.action !== action)) {
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

/** 视图命中的第一条错误：沿用 scope 匹配语义——包精确命中或按着装兜底（未选包时也能看到本外观的错误）。 */
export function scopedGenError(
  errors: Record<string, VideoGenError>,
  outfitId: number | null,
  packId: number | null,
  action?: string | null
): VideoGenError | null {
  for (const error of Object.values(errors)) {
    if (videoGenScopeMatches(error, outfitId, packId, action)) {
      return error
    }
  }

  return null
}

function resolveGenScope(
  opts: { outfitId?: number | null; sourcePackId?: number; retryPackId?: number; action?: string | null },
  packId?: number | null
): VideoGenScope {
  const resolvedPackId = packId ?? opts.sourcePackId ?? opts.retryPackId ?? null
  const pack = resolvedPackId != null ? $videoPacks.get().find(p => p.id === resolvedPackId) : null

  return {
    outfitId: opts.outfitId ?? pack?.outfit_id ?? null,
    packId: resolvedPackId,
    action: opts.action ?? null
  }
}

function eventScope(scope: Partial<VideoGenScope>): VideoGenScope {
  return { outfitId: scope.outfitId ?? null, packId: scope.packId ?? null, action: scope.action ?? null }
}

/** 就绪 / 激活事件：整组就绪可收敛已结束动作的错误，激活只清同 scope。 */
export function videoGenReady(scope: Partial<VideoGenScope>, clearGroupActionErrors = false): void {
  videoPackEventReceived(scope)
  const resolved = resolveGenScope({ outfitId: scope.outfitId, action: scope.action }, scope.packId)

  clearScopedErrors(resolved, clearGroupActionErrors)

  if (resolved.packId != null) {
    setScopedStage(resolved.packId, null, resolved.action)
  }

  $videoGenScope.set(resolved)
  refreshDerivedState()
}

/** 生成阶段事件：未知阶段按无阶段处理；事件缺少着装时依次取包列表、同一动作包上次的归属。 */
export function videoGenProgress(
  stage: string | undefined,
  scope: Partial<VideoGenScope>,
  videoQueue: VideoQueueState | null = null
): void {
  const resolved = resolveGenScope({ outfitId: scope.outfitId, action: scope.action }, scope.packId)
  const previous = $videoGenScope.get()

  videoPackEventReceived(scope)
  clearScopedErrors(resolved)

  $videoPacks.set(
    $videoPacks.get().map(pack =>
      pack.id === resolved.packId
        ? {
            ...pack,
            actions: pack.actions.map(action =>
              action.action === resolved.action
                ? {
                    ...action,
                    video_queue: videoQueue ? { ...videoQueue, next_retry_at: videoQueue.next_retry_at ?? null } : null
                  }
                : action
            )
          }
        : pack
    )
  )

  if (resolved.packId != null) {
    setScopedStage(resolved.packId, VIDEO_GEN_STAGES.find(known => known === stage) ?? null, resolved.action)
  }

  $videoGenScope.set({
    outfitId:
      resolved.outfitId ??
      (resolved.packId !== null && previous?.packId === resolved.packId ? previous.outfitId : null),
    packId: resolved.packId,
    action: resolved.action
  })
  refreshDerivedState()
}

/** 生成失败事件：文案取后端公开原因，缺省用通用提示。 */
export function videoGenFailed(reason: string | undefined, scope: Partial<VideoGenScope>): void {
  videoPackEventReceived(scope)
  const resolved = eventScope(scope)

  if (resolved.packId != null) {
    setScopedStage(resolved.packId, null, resolved.action)
  }

  setScopedError(resolved, reason || getStrings().living.appearance.videoGenRequestFailed, 'generation')
}

registerStorageClearHandler(() => {
  inflight = null
  generationRevision += 1
  scopeRevisions.clear()
  activeStages.clear()
  $videoGenRequests.set({})
  $videoPacks.set([])
  $videoGenState.set('idle')
  $videoGenStages.set({})
  $videoGenErrors.set({})
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
      const res = await authedResource('GET /api/companion/video-packs')

      if (epoch !== currentClearEpoch() || revision !== generationRevision) {
        return false
      }

      if (!apiSucceeded(res, 'video-pack-store', 'hydrateVideoPack failed')) {
        return false
      }

      if (!res.value) {
        return false
      }

      const packs = res.value.packs
      $videoPacks.set(packs)

      // 请求拒绝不因列表仍为 ready 而消失；生成失败在回源确认恢复后清除。
      const errors = { ...$videoGenErrors.get() }

      for (const [key, error] of Object.entries(errors)) {
        const related = packs.filter(pack => videoGenScopeMatches(error, pack.outfit_id, pack.id))

        if (
          (error.packId != null && related.length === 0) ||
          (error.source === 'generation' && related.length > 0 && related.every(pack => pack.status === 'ready'))
        ) {
          delete errors[key]
        }
      }

      for (const pack of packs) {
        const scope = { outfitId: pack.outfit_id, packId: pack.id, action: null }
        const key = scopeKey(scope)

        if (pack.status === 'failed' && !errors[key]) {
          errors[key] = {
            ...scope,
            message: pack.error || getStrings().living.appearance.videoGenRequestFailed,
            source: 'generation'
          }
        }
      }

      $videoGenErrors.set(errors)

      clearStaleStages()
      refreshDerivedState()

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

/** 发起按参考生成，进度与结果经 companion.video 事件回流；同一动作在结果确定前拒绝重复提交。 */
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

  const scope = resolveGenScope(opts)
  const key = scope.action == null && scope.outfitId != null ? `outfit:${scope.outfitId}` : scopeKey(scope)

  const relatedPacks = $videoPacks
    .get()
    .filter(pack => (scope.outfitId != null ? pack.outfit_id === scope.outfitId : pack.id === scope.packId))

  const requestPending = Object.values($videoGenRequests.get()).some(request =>
    request.outfitId != null ? request.outfitId === scope.outfitId : request.packId === scope.packId
  )

  const retryPack = opts.retryPackId != null ? relatedPacks.find(pack => pack.id === opts.retryPackId) : null

  // 恢复只推进后端明确允许续查的任务；同组未知结果不阻止其他已知任务安全恢复。
  const busy =
    opts.retryPackId != null
      ? !retryPack?.can_retry || requestPending || videoPackIsGenerating(retryPack)
      : scope.action != null
        ? videoActionIsBusy($videoPacks.get(), $videoGenRequests.get(), scope.outfitId, scope.packId, scope.action)
        : requestPending ||
          relatedPacks.some(
            pack =>
              pack.status === 'processing' ||
              pack.actions.some(action => videoActionIsPending(action.status) || action.can_regenerate === false)
          )

  if (busy) {
    // 仅记录本次被拒请求的归属，不把进行中的任务改写成 failed。
    setScopedError(scope, getStrings().living.appearance.videoGenBusy)
    refreshDerivedState()

    return false
  }

  if (
    opts.force &&
    scope.action == null &&
    relatedPacks.some(pack => pack.actions.some(action => action.has_ready_asset))
  ) {
    setScopedError(scope, getStrings().living.appearance.videoPackHasReadyActions)

    return false
  }

  const epoch = currentClearEpoch()
  const revision = generationRevision
  $videoGenRequests.set({ ...$videoGenRequests.get(), [key]: scope })
  clearScopedErrors(scope)
  refreshDerivedState()

  try {
    const res = opts.retryPackId
      ? await authedResource('POST /api/companion/video-packs/{pack_id}/production', {
          path: { pack_id: opts.retryPackId },
          body: { command: 'retry' }
        })
      : await authedResource('POST /api/companion/video-packs', {
          body: {
            source: 'generate',
            force: opts.force === true,
            outfit_id: opts.outfitId,
            source_pack_id: opts.sourcePackId,
            action: opts.action,
            feedback: opts.feedback
          }
        })

    if (epoch !== currentClearEpoch()) {
      return false
    }

    if (!res.ok) {
      if (res.reason === 'err' && scopeRevision(scope) <= revision) {
        setScopedError(scope, backendDetailMessage(res.error, getStrings().living.appearance.videoGenRequestFailed))
        refreshDerivedState()
      }

      return false
    }

    if (!res.value) {
      return false
    }

    const resolvedScope = resolveGenScope({ ...opts, outfitId: opts.outfitId ?? res.value.outfit_id }, res.value.id)

    if (scopeRevision(resolvedScope) <= revision) {
      clearScopedErrors(resolvedScope)
      $videoGenScope.set(resolvedScope)

      if (res.value.status === 'processing') {
        setScopedStage(res.value.id, 'script', resolvedScope.action)
      }
    }

    refreshDerivedState()

    await hydrateVideoPack(true)

    return true
  } finally {
    if (epoch === currentClearEpoch()) {
      const requests = { ...$videoGenRequests.get() }
      delete requests[key]
      $videoGenRequests.set(requests)
      refreshDerivedState()
    }
  }
}

export async function activateVideoPack(packId: number): Promise<boolean> {
  const epoch = currentClearEpoch()
  const res = await authedResource('PUT /api/companion/video-packs/current', { body: { pack_id: packId } })

  if (epoch !== currentClearEpoch()) {
    return false
  }

  if (!res.ok) {
    if (res.reason === 'err') {
      // 穿着失败也要进入失败态，界面才能标红并展示原因。
      setScopedError(
        resolveGenScope({}, packId),
        backendDetailMessage(res.error, getStrings().living.appearance.videoActivateFailed)
      )
      refreshDerivedState()
    }

    return false
  }

  $videoGenScope.set(null)
  clearScopedErrors(resolveGenScope({}, packId))
  setScopedStage(packId, null)
  refreshDerivedState()
  await hydrateVideoPack(true)

  return true
}
