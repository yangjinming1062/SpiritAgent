import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { authedResource, requestResource } from '@/shared/lib/resource-request'
import { currentClearEpoch, definePersistedAtom, registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { isRecord } from '@client-shared/is-record'

import type { ApiSchemas } from '../../../../../shared/api.generated'

type OutfitStatus = ApiSchemas['OutfitResponse']['status']

interface WardrobeOutfit {
  initialVideoError: string | null
  id: number
  name: string
  description: string | null
  descriptionStatus: 'pending' | 'processing' | 'ready' | 'failed'
  descriptionError: string | null
  fullbodyPath: string | null
  fullbodyUrl: string | null
  status: OutfitStatus
  active: boolean
}

type OutfitResponse = ApiSchemas['OutfitResponse']

export type OutfitPolicy = ApiSchemas['OutfitPolicyResponse']['policy']

type WardrobeSnapshot = ApiSchemas['OutfitListResponse']

const snapshot = definePersistedAtom<WardrobeSnapshot>({
  key: 'da.companion.wardrobe',
  fallback: { outfits: [], policy: 'llm_may_replace' },
  isPersistable: (value: unknown): value is WardrobeSnapshot =>
    isRecord(value) &&
    Array.isArray(value.outfits) &&
    value.outfits.every(isOutfitResponse) &&
    (value.policy === 'locked' || value.policy === 'llm_may_replace')
})

function isOutfitResponse(value: unknown): value is OutfitResponse {
  return (
    isRecord(value) &&
    typeof value.id === 'number' &&
    typeof value.name === 'string' &&
    (value.description === null || typeof value.description === 'string') &&
    typeof value.description_status === 'string' &&
    ['pending', 'processing', 'ready', 'failed'].includes(value.description_status) &&
    (value.description_error === null || typeof value.description_error === 'string') &&
    (value.initial_video_error === null || typeof value.initial_video_error === 'string') &&
    typeof value.fullbody_url === 'string' &&
    typeof value.status === 'string' &&
    ['draft', 'ready', 'expired'].includes(value.status) &&
    typeof value.active === 'boolean'
  )
}

export const $outfits = atom<WardrobeOutfit[]>([])
export const $outfitPolicy = atom<OutfitPolicy>(snapshot.get().policy)
export const $wardrobeStatus = atom<'idle' | 'loading' | 'loaded' | 'error'>('idle')
let revision = 0
let policyRevision = 0

registerStorageClearHandler(() => {
  revision += 1
  policyRevision += 1
  $outfits.set([])
  $outfitPolicy.set('llm_may_replace')
  $wardrobeStatus.set('idle')
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
        description: o.description,
        descriptionStatus: o.description_status,
        descriptionError: o.description_error,
        initialVideoError: o.initial_video_error,
        fullbodyPath,
        fullbodyUrl: old?.fullbodyPath === fullbodyPath ? old.fullbodyUrl : null,
        status: o.status,
        active: o.active
      }
    })
  )

  await Promise.all(
    state.outfits.map(async (o): Promise<void> => {
      if (!o.fullbody_url) {
        return
      }

      try {
        const url = await backendApi().apiAsset({ url: o.fullbody_url, preferCache: true, cacheOnly })

        if (url && current()) {
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

let wardrobeRequest: Promise<void> | null = null

/** 衣柜列表读取；并发调用共享同一次请求，避免同一页面重复拉取。 */
export async function hydrateWardrobe(): Promise<void> {
  if (wardrobeRequest) {
    return wardrobeRequest
  }

  const request = (async (): Promise<void> => {
    const scope = captureAuthScope()

    if (!scope) {
      return
    }

    const policyVersion = policyRevision
    const version = ++revision
    const current = (): boolean => version === revision && scope()

    $wardrobeStatus.set('loading')

    if ($outfits.get().length === 0) {
      await showOutfits(snapshot.get(), version, true)
    }

    const result = await authedResource('companion.get_outfits')

    if (!current()) {
      return
    }

    if (!result.ok || !result.value) {
      if (!result.ok && result.reason === 'err' && !isClientErrorIpc(result.error)) {
        log.warn('wardrobe', 'hydrateWardrobe failed', result.error)
      }

      $wardrobeStatus.set('error')

      return
    }

    const state = {
      outfits: result.value.outfits,
      policy: policyVersion !== policyRevision ? $outfitPolicy.get() : result.value.policy
    }

    snapshot.set(state)
    await showOutfits(state, version, false)

    if (current()) {
      $wardrobeStatus.set('loaded')
    }
  })()

  wardrobeRequest = request

  try {
    await request
  } finally {
    if (wardrobeRequest === request) {
      wardrobeRequest = null
    }
  }
}

export async function setOutfitPolicy(policy: OutfitPolicy): Promise<boolean> {
  const epoch = currentClearEpoch()

  try {
    const result = await requestResource('companion.patch_outfit_policy', { body: { policy } })

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

export async function deleteOutfit(outfitId: number): Promise<void> {
  await requestResource('companion.delete_outfit_route', { path: { outfit_id: outfitId } })
  await hydrateWardrobe()
}

export async function retryOutfitDescription(outfitId: number): Promise<void> {
  await requestResource('companion.post_retry_outfit_description', { path: { outfit_id: outfitId } })
  await hydrateWardrobe()
}
