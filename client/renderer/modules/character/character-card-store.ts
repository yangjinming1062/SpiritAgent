import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'

import { $activeAvatarId } from './portrait-store'

export const PORTRAIT_FEATURE_KEYS = ['head_shape', 'facial_features', 'facial_surface', 'head_identifiers'] as const
export const BODY_FEATURE_KEYS = ['body_shape', 'proportions', 'limbs_and_appendages', 'body_surface'] as const
export type CharacterFeatureKey = (typeof PORTRAIT_FEATURE_KEYS)[number] | (typeof BODY_FEATURE_KEYS)[number]
export type CharacterFeatures = Record<CharacterFeatureKey, string>
export type CharacterOverrides = Partial<Record<CharacterFeatureKey, string | null>>
export type ExtractionStatus = 'pending' | 'running' | 'ready' | 'failed'

export interface CharacterCard {
  avatar_id: number
  revision: number
  features: CharacterFeatures
  automatic: CharacterFeatures
  overrides: CharacterOverrides
  status: ExtractionStatus
  portrait_status: ExtractionStatus
  body_status: ExtractionStatus
  error: string | null
}

export const $characterCard = atom<CharacterCard | null>(null)
let requestGeneration = 0

registerStorageClearHandler(() => {
  requestGeneration += 1
  $characterCard.set(null)
})

$activeAvatarId.listen(() => {
  requestGeneration += 1
  $characterCard.set(null)
})

function publish(card: CharacterCard | null): void {
  const avatarId = $activeAvatarId.get()

  if (card && avatarId !== null && card.avatar_id !== avatarId) {
    return
  }

  const current = $characterCard.get()

  if (card && current?.avatar_id === card.avatar_id && current.revision > card.revision) {
    return
  }

  $characterCard.set(card)
}

export async function hydrateCharacterCard(): Promise<void> {
  const generation = ++requestGeneration
  const result = await authedApi<CharacterCard>({ path: '/api/companion/character-card' })

  if (generation !== requestGeneration) {
    return
  }

  if (!result.ok) {
    if (result.reason === 'err') {
      throw result.error
    }

    return
  }

  publish(result.value)
}

function publishMutation(card: CharacterCard | null): void {
  requestGeneration += 1
  publish(card)
  // 分析状态可能在写响应返回前已变化，重新读取以免覆盖先到的事件刷新。
  void hydrateCharacterCard().catch(error => log.warn('character-card', 'Refresh failed', error))
}

export async function saveCharacterCard(
  expectedAvatarId: number,
  expectedRevision: number,
  changes: CharacterOverrides
): Promise<boolean> {
  const epoch = currentClearEpoch()
  const avatarId = $activeAvatarId.get()

  const result = await authedApi<CharacterCard>({
    body: { changes, expected_avatar_id: expectedAvatarId, expected_revision: expectedRevision },
    method: 'PATCH',
    path: '/api/companion/character-card'
  })

  if (!result.ok) {
    if (result.reason === 'err') {
      throw result.error
    }

    return false
  }

  if (epoch !== currentClearEpoch() || avatarId !== $activeAvatarId.get() || !result.value) {
    return false
  }

  publishMutation(result.value)

  return true
}

export async function extractCharacterCard(expectedAvatarId: number, expectedRevision: number): Promise<void> {
  const epoch = currentClearEpoch()
  const avatarId = $activeAvatarId.get()

  const result = await authedApi<CharacterCard>({
    body: { expected_avatar_id: expectedAvatarId, expected_revision: expectedRevision },
    method: 'POST',
    path: '/api/companion/character-card/extract'
  })

  if (!result.ok) {
    if (result.reason === 'err') {
      throw result.error
    }

    return
  }

  if (epoch === currentClearEpoch() && avatarId === $activeAvatarId.get()) {
    publishMutation(result.value)
  }
}
