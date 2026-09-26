import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { safeJsonParse } from '@/shared/lib/safe-json'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import { personaFromWire } from './persona-mappers'

export interface PersonaDefinition {
  name: string
  personality: string
  speakingStyle: string
  relationship?: string
  biological_type?: string
  gender?: string
}

export const $persona = atom<PersonaDefinition | null>(null)
export const $personalityTags = atom<string[]>([])
export const $companionMood = atom<string | null>(null)

function resetPersona(): void {
  $persona.set(null)
  $personalityTags.set([])
  $companionMood.set(null)
}

registerStorageClearHandler(resetPersona)

export async function hydratePersona(opts: { silent?: boolean } = {}): Promise<{ ok: boolean; error?: unknown }> {
  const auth = $auth.get()

  if (auth.kind !== 'authenticated') {
    return { ok: false }
  }

  const sessionId = auth.snapshot.sessionId
  const epoch = currentClearEpoch()

  const isCurrent = (): boolean => {
    const current = $auth.get()

    return current.kind === 'authenticated' && current.snapshot.sessionId === sessionId && epoch === currentClearEpoch()
  }

  // 全部结构化 persona 字段都在 definition_json（JSON 字符串 blob）里面，
  // 而不是作为顶层扁平 key 出现在线协议里。
  const result = await authedApi<{
    definition_json?: string
    is_complete?: boolean
    personality_tags?: string[]
    current_mood?: string | null
  }>({
    path: '/api/companion/persona'
  })

  if (!isCurrent()) {
    return { ok: false }
  }

  if (!result.ok) {
    if (result.reason === 'unauth') {
      return { ok: false }
    }

    // 调用方刚刚成功 PUT 了新 persona 时，这里的 GET 短暂失败不代表保存失败——
    // 后端是有数据的。传 `silent: true` 保持 $persona 不动，避免同时弹出「保存失败」提示
    // 又让设置页因为 $persona 变 null 而隐藏「编辑」按钮。GET 失败由调用方作为软提示暴露。
    if (!opts.silent) {
      $persona.set(null)
      $personalityTags.set([])
      $companionMood.set(null)
    }

    return { error: result.error, ok: false }
  }

  const p = result.value

  if (!p) {
    return { ok: false }
  }

  // 早返回：先看一眼 p.is_complete，避开未设置 persona 的合法空态。
  if (!p.is_complete) {
    // 「还没设置 persona」是合法状态，不是错误：保持 $persona 不动（不要置空），
    // 这样「保存刚刚成功，hydrate 落地却读到陈旧 is_complete」的竞态，
    // 不会让那些依赖 $persona 的消费者把它当成清空。
    return { ok: true }
  }

  const parsed = safeJsonParse<Record<string, string>>(p.definition_json, {})

  if (!isCurrent()) {
    return { ok: false }
  }

  $persona.set(
    personaFromWire({
      biological_type: parsed.biological_type,
      gender: parsed.gender,
      name: parsed.name ?? '伙伴',
      personality: parsed.personality ?? '',
      relationship: parsed.relationship,
      speaking_style: parsed.speaking_style
    })
  )

  $personalityTags.set(p.personality_tags ?? [])

  $companionMood.set(p.current_mood?.trim() || null)

  return { ok: true }
}
