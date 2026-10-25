import { atom } from 'nanostores'

import type { AuthedApiResult } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { authedResource } from '@/shared/lib/resource-request'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import type { CharacterCard, CharacterFeatureKey, CharacterOverrides } from '@protocol'

import { $activeAvatarId } from './portrait-store'

export const PORTRAIT_FEATURE_KEYS = [
  'head_shape',
  'facial_features',
  'facial_surface',
  'head_identifiers'
] as const satisfies readonly CharacterFeatureKey[]
export const BODY_FEATURE_KEYS = [
  'body_shape',
  'proportions',
  'limbs_and_appendages',
  'body_surface'
] as const satisfies readonly CharacterFeatureKey[]
export const $characterCard = atom<CharacterCard | null>(null)
let requestGeneration = 0

function resetCard(): void {
  requestGeneration += 1
  $characterCard.set(null)
}

registerStorageClearHandler(resetCard)
$activeAvatarId.listen(resetCard)

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
  const result = await authedResource('companion.get_character_card_route')

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

function publishMutation(card: CharacterCard): void {
  publish(card)
  // 分析状态可能在写响应返回前已变化，重新读取以免覆盖先到的事件刷新。
  void hydrateCharacterCard().catch(error => log.warn('character-card', 'Refresh failed', error))
}

// 写请求：请求错误抛出；未登录、发起后换号或换形象、响应无内容时返回 false，成功则发布响应并返回 true。
async function mutateCard(write: () => Promise<AuthedApiResult<CharacterCard>>): Promise<boolean> {
  const epoch = currentClearEpoch()
  const avatarId = $activeAvatarId.get()
  const result = await write()

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

export function saveCharacterCard(
  expectedAvatarId: string,
  expectedRevision: number,
  changes: CharacterOverrides
): Promise<boolean> {
  return mutateCard(() =>
    authedResource('companion.patch_character_card_route', {
      body: { changes, expected_avatar_id: expectedAvatarId, expected_revision: expectedRevision }
    })
  )
}

export async function extractCharacterCard(expectedAvatarId: string, expectedRevision: number): Promise<void> {
  await mutateCard(() =>
    authedResource('companion.extract_character_card_route', {
      body: { expected_avatar_id: expectedAvatarId, expected_revision: expectedRevision }
    })
  )
}
