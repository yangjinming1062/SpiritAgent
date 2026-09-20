import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, definePersistedAtom, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

type OutfitStatus = 'draft' | 'ready' | 'failed' | 'expired'

interface WardrobeOutfit {
  id: number
  name: string
  description: string | null
  fullbodyPath: string | null
  fullbodyUrl: string | null
  status: OutfitStatus
  active: boolean
}

interface OutfitResponse {
  id: number
  name: string
  description: string | null
  fullbody_url: string
  status: string
  active: boolean
}

export type OutfitPolicy = 'llm_may_replace' | 'locked'

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
let revision = 0
let policyRevision = 0

registerStorageClearHandler(() => {
  revision += 1
  policyRevision += 1
  $outfits.set([])
  $outfitPolicy.set('llm_may_replace')
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
        status: (o.status || 'draft') as OutfitStatus,
        active: o.active === true
      }
    })
  )

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

/** 穿着就绪外观：翻转当前生效着装描述（房间 / 出镜媒体消费），不触发任何生成。 */
export async function activateOutfit(outfitId: number): Promise<boolean> {
  try {
    await window.spiritagent.api({ path: `/api/companion/outfits/${outfitId}/activate`, method: 'PUT' })
    await hydrateWardrobe()

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
