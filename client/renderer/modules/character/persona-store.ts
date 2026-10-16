import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { authedResource } from '@/shared/lib/resource-request'
import { safeJsonParse } from '@/shared/lib/safe-json'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import type { PersonaResponse } from '@protocol'

import type { PersonaPayload } from './persona'

export const $persona = atom<PersonaPayload | null>(null)
export const $personalityTags = atom<string[]>([])
export const $companionMood = atom<string | null>(null)
let requestGeneration = 0

function resetPersona(): void {
  requestGeneration += 1
  $persona.set(null)
  $personalityTags.set([])
  $companionMood.set(null)
}

registerStorageClearHandler(resetPersona)

export function applyPersonaResponse(response: PersonaResponse): void {
  requestGeneration += 1

  if (!response.is_complete) {
    return
  }

  const parsed = safeJsonParse<Record<string, string>>(response.definition_json, {})

  $persona.set({
    biological_type: parsed.biological_type,
    gender: parsed.gender,
    name: parsed.name ?? '',
    personality: parsed.personality ?? '',
    relationship: parsed.relationship,
    speaking_style: parsed.speaking_style ?? ''
  })
  $personalityTags.set(response.personality_tags)
  $companionMood.set(response.current_mood?.trim() || null)
}

export async function hydratePersona(opts: { silent?: boolean } = {}): Promise<{ ok: boolean; error?: unknown }> {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return { ok: false }
  }

  const generation = ++requestGeneration

  const result = await authedResource('GET /api/companion/persona')

  if (!isCurrent() || generation !== requestGeneration) {
    return { ok: false }
  }

  if (!result.ok) {
    if (result.reason === 'unauth') {
      return { ok: false }
    }

    if (!opts.silent) {
      resetPersona()
    }

    return { error: result.error, ok: false }
  }

  if (!result.value) {
    return { ok: false }
  }

  applyPersonaResponse(result.value)

  return { ok: true }
}
