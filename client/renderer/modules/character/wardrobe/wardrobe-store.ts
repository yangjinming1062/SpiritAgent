import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { isClientErrorIpc, unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, definePersistedAtom, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import { cachePuppetAssetPack, hydrateMesh2D, type PuppetAssetSource } from '../rendering/2d'

type OutfitStatus = 'draft' | 'splitting' | 'ready' | 'failed' | 'expired'

interface WardrobeOutfit {
  asset: PuppetAssetSource | null
  id: number
  name: string
  description: string | null
  fullbodyPath: string | null
  fullbodyUrl: string | null
  style: string
  status: OutfitStatus
  active: boolean
  pendingWear: boolean
}

interface OutfitResponse {
  asset?: PuppetAssetSource | null
  id: number
  name: string
  description: string | null
  fullbody_url: string
  style: string
  status: string
  active: boolean
  pending_wear: boolean
}

export type OutfitPolicy = 'llm_may_replace' | 'locked'

export type PoseRegenSide = 'left' | 'right'

export interface PoseRegenState {
  outfitId: number
  side: PoseRegenSide
  baselineHash: string | null
  /** null=生成中；非空=已失败（空串表示无服务端文案，仅展示通用失败） */
  error: string | null
}

interface WardrobeSnapshot {
  outfits: OutfitResponse[]
  policy: OutfitPolicy
}

const snapshot = definePersistedAtom<WardrobeSnapshot>({
  key: 'da.companion.wardrobe',
  fallback: { outfits: [], policy: 'llm_may_replace' },
  isPersistable: (value: unknown): value is WardrobeSnapshot => {
    if (!value || typeof value !== 'object') {
      return false
    }

    const state = value as WardrobeSnapshot

    return (
      Array.isArray(state.outfits) &&
      state.outfits.every(o => o && typeof o.id === 'number' && typeof o.fullbody_url === 'string') &&
      (state.policy === 'locked' || state.policy === 'llm_may_replace')
    )
  }
})

export const $outfits = atom<WardrobeOutfit[]>([])
export const $outfitPolicy = atom<OutfitPolicy>(snapshot.get().policy)
export const $poseRegen = atom<PoseRegenState | null>(null)
const warmed = new Set<string>()
let revision = 0
let policyRevision = 0
let poseRegenTimer: ReturnType<typeof setTimeout> | undefined

registerStorageClearHandler(() => {
  revision += 1
  policyRevision += 1
  warmed.clear()
  $outfits.set([])
  $outfitPolicy.set('llm_may_replace')
  $poseRegen.set(null)
})

async function showOutfits(state: WardrobeSnapshot, version: number, cacheOnly: boolean): Promise<void> {
  const epoch = currentClearEpoch()

  const current = (): boolean =>
    version === revision && epoch === currentClearEpoch() && $auth.get().kind === 'authenticated'

  if (!current()) {
    return
  }

  const previous = new Map($outfits.get().map(outfit => [outfit.id, outfit]))
  $outfitPolicy.set(state.policy)
  $outfits.set(
    state.outfits.map((o): WardrobeOutfit => {
      const old = previous.get(o.id)
      const fullbodyPath = o.fullbody_url.split('?', 1)[0] || null

      return {
        id: o.id,
        name: o.name,
        description: o.description ?? null,
        fullbodyPath,
        fullbodyUrl: old?.fullbodyPath === fullbodyPath ? old.fullbodyUrl : null,
        style: o.style || 'refined_anime_cg',
        status: (o.status || 'draft') as OutfitStatus,
        active: o.active === true,
        pendingWear: o.pending_wear === true,
        asset:
          o.asset?.content_hash && old?.asset?.content_hash === o.asset.content_hash ? old.asset : (o.asset ?? null)
      }
    })
  )
  // 单侧姿态重生成完成信号：外观还在且 content_hash 已离开基线（hash 必变）；外观被删也视为结束
  const regen = $poseRegen.get()

  if (regen && regen.error === null) {
    const updated = state.outfits.find(o => o.id === regen.outfitId)

    if (!updated || updated.asset?.content_hash !== regen.baselineHash) {
      $poseRegen.set(null)
    }
  }

  await Promise.all(
    state.outfits.map(async (o): Promise<void> => {
      if (!o.fullbody_url) {
        return
      }

      try {
        const url = await window.spiritagent.apiAsset({ url: o.fullbody_url, preferCache: true, cacheOnly })

        if (current()) {
          $outfits.set($outfits.get().map(item => (item.id === o.id ? { ...item, fullbodyUrl: url } : item)))
        }
      } catch (err) {
        if (!cacheOnly && current() && !isClientErrorIpc(err)) {
          log.warn('wardrobe', 'outfit image unavailable', err)
        }
      }
    })
  )
}

async function warmAssets(outfits: OutfitResponse[], epoch: number): Promise<void> {
  for (const outfit of outfits) {
    if (epoch !== currentClearEpoch() || $auth.get().kind !== 'authenticated') {
      return
    }

    const source = outfit.asset
    const key = source?.content_hash ?? source?.manifest_url

    if (!source || !key || warmed.has(key)) {
      continue
    }

    warmed.add(key)

    try {
      await cachePuppetAssetPack(source)
    } catch (err) {
      warmed.delete(key)
      log.warn('wardrobe', 'asset cache warmup failed', err)
    }
  }
}

export async function hydrateWardrobe(): Promise<void> {
  if ($auth.get().kind !== 'authenticated') {
    return
  }

  const policyVersion = policyRevision
  const version = ++revision
  const epoch = currentClearEpoch()

  if ($outfits.get().length === 0) {
    await showOutfits(snapshot.get(), version, true)
  }

  const result = await authedApi<WardrobeSnapshot>({ path: '/api/companion/outfits' })

  if (version !== revision || epoch !== currentClearEpoch()) {
    return
  }

  if (!result.ok || !result.value) {
    if (!result.ok && result.reason === 'err' && !isClientErrorIpc(result.error)) {
      log.warn('wardrobe', 'hydrateWardrobe failed', result.error)
    }

    return
  }

  const state = {
    outfits: result.value.outfits ?? [],
    policy:
      policyVersion !== policyRevision
        ? $outfitPolicy.get()
        : result.value.policy === 'locked'
          ? ('locked' as const)
          : ('llm_may_replace' as const)
  }

  snapshot.set(state)
  await showOutfits(state, version, false)

  if (version === revision && epoch === currentClearEpoch()) {
    void warmAssets(state.outfits, epoch)
  }
}

export async function setOutfitPolicy(policy: OutfitPolicy): Promise<boolean> {
  const epoch = currentClearEpoch()

  try {
    const result = await window.spiritagent.api<{ policy: OutfitPolicy }>({
      body: { policy },
      method: 'PATCH',
      path: '/api/companion/outfits/policy'
    })

    if (epoch !== currentClearEpoch() || $auth.get().kind !== 'authenticated') {
      return false
    }

    policyRevision += 1
    $outfitPolicy.set(result.policy)
    snapshot.set({ policy: result.policy })

    return true
  } catch (err) {
    log.warn('wardrobe', 'setOutfitPolicy failed', err)

    return false
  }
}

/** 穿着就绪外观；成功后整包替换 2D 资产（PuppetStage 按 PSD 重建，期间旧装不断档）。 */
export async function activateOutfit(outfitId: number): Promise<boolean> {
  try {
    await window.spiritagent.api({ path: `/api/companion/outfits/${outfitId}/activate`, method: 'PUT' })
    await hydrateWardrobe()
    await hydrateMesh2D()

    return true
  } catch (err) {
    log.warn('wardrobe', 'activateOutfit failed', err)

    return false
  }
}

export async function deleteOutfit(outfitId: number): Promise<boolean> {
  try {
    await window.spiritagent.api({ path: `/api/companion/outfits/${outfitId}`, method: 'DELETE' })
    await hydrateWardrobe()

    return true
  } catch (err) {
    log.warn('wardrobe', 'deleteOutfit failed', err)

    return false
  }
}

// 主进程错误含状态码、路径与 JSON 错误体，取 detail 里的公开文案；解析不了就留空（UI 走通用失败文案）
function poseRegenErrMsg(err: unknown): string {
  try {
    const parsed = JSON.parse(unwrapIpcErrorMessage(err).replace(/^\d{3}\s+(?:\/[^\s]*:\s*)?/, '')) as {
      detail?: { error?: unknown }
    }

    if (typeof parsed?.detail?.error === 'string' && parsed.detail.error) {
      return parsed.detail.error
    }
  } catch {
    /* 非预期形态，走通用失败文案 */
  }

  return ''
}

/** 单侧重生成一侧扶边姿态：请求只负责校验入队，完成与失败由 WS 事件驱动
 * （content_hash 变化 / companion.outfit.failed）；兜底超时防后端重启丢任务后 pending 永挂。 */
export async function regenerateOutfitPose(outfitId: number, side: PoseRegenSide): Promise<boolean> {
  const baselineHash = $outfits.get().find(o => o.id === outfitId)?.asset?.content_hash ?? null
  $poseRegen.set({ outfitId, side, baselineHash, error: null })
  clearTimeout(poseRegenTimer)
  poseRegenTimer = setTimeout(() => {
    const regen = $poseRegen.get()

    if (regen?.outfitId === outfitId && regen.error === null) {
      $poseRegen.set({ ...regen, error: '' })
    }
  }, 30 * 60_000)

  try {
    await window.spiritagent.api({
      method: 'POST',
      path: `/api/companion/outfits/${outfitId}/poses/${side}/regenerate`
    })

    return true
  } catch (err) {
    log.warn('wardrobe', 'regenerateOutfitPose failed', err)
    const regen = $poseRegen.get()

    if (regen?.outfitId === outfitId && regen.error === null) {
      $poseRegen.set({ ...regen, error: poseRegenErrMsg(err) })
    }

    return false
  }
}

/** WS companion.outfit.failed：匹配在飞单侧重生成时落失败态（保留条目供 UI 展示原因）。 */
export function failPoseRegen(outfitId: number | undefined, reason: string | null | undefined): void {
  const regen = $poseRegen.get()

  if (regen && regen.error === null && regen.outfitId === outfitId) {
    $poseRegen.set({ ...regen, error: reason || '' })
  }
}
