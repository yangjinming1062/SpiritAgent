import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { safeJsonParse } from '@/shared/lib/safe-json'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import { personaFromWire } from './persona-mappers'
import { $renderMode, setRenderMode } from './render-mode'

export interface PersonaDefinition {
  name: string
  personality: string
  speakingStyle: string
  relationship?: string
  biological_type?: string
  gender?: string
  appearance?: string
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
  // 全部结构化 persona 字段都在 definition_json（JSON 字符串 blob）里面，
  // 而不是作为顶层扁平 key 出现在线协议里。
  const result = await authedApi<{
    definition_json?: string
    is_complete?: boolean
    personality_tags?: string[]
    render_mode?: string
    current_mood?: string | null
  }>({
    path: '/api/companion/persona'
  })

  if (!result.ok) {
    if (result.reason === 'unauth') {
      return { ok: false }
    }

    // C2：调用方刚刚成功 PUT 了新 persona 时，这里的 GET 短暂失败不代表保存失败——
    // 后端是有数据的。传 `silent: true` 保持 $persona 不动，避免同时弹出「保存失败」提示
    // 又让设置页因为 $persona 变 null 而隐藏「编辑」按钮。GET 失败由调用方作为软提示暴露。
    if (!opts.silent && $auth.get().kind === 'authenticated') {
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

  // 必须在所有持久化写入之前做第二次 auth 检查：登出 race 里 response 已经返回，
  // setRenderMode 把 stale 值写进刚 clearCompanionStorage 清空的 localStorage——
  // 下一位用户读到错误的 renderMode。第二道闸门同时守护 setRenderMode / $persona.set /
  // $personalityTags.set 三处写入（中间无 await，原子性由 JS 单线程保证）。
  if ($auth.get().kind !== 'authenticated') {
    return { ok: false }
  }

  if ((p.render_mode === '3d' || p.render_mode === '2d') && p.render_mode !== $renderMode.get()) {
    setRenderMode(p.render_mode)
  }

  $persona.set(
    personaFromWire({
      appearance: parsed.appearance,
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
