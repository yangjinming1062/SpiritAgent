import { atom, type WritableAtom } from 'nanostores'

import { setDisturbanceTier } from '@/companion/companion-store'
import { persistBoolean, persistString, storedBoolean, storedString } from '@/shared/lib/storage'

// 响应模式控制伙伴在 Chat 模式下如何回复（DESIGN §6.1 响应模式）。
export type ResponseMode = 'text' | 'voice'

// localStorage 仍是各窗口的即时缓存（同步读、离线可用）；每次写入额外经
// prefs:set 通道上报主进程，并入 companion.* 云同步节（云端真源，PROTOCOL §2.4）。
// 水合广播（initCompanionPrefsSync）用云端值回写缓存与 atom，跨端收敛。
function reportCloud(key: string, value: unknown): void {
  window.spiritagent?.prefs?.set({ key, value })
}

export const $companionVoiceId = atom<string>(storedString('da.companion.voiceId') ?? '')
export const $responseMode = atom<ResponseMode>((storedString('da.companion.responseMode') as ResponseMode) ?? 'text')

export function setCompanionVoiceId(voice: string): void {
  $companionVoiceId.set(voice)
  persistString('da.companion.voiceId', voice || null)
  reportCloud('companion.voice_id', voice)
}

export function setResponseMode(mode: ResponseMode): void {
  $responseMode.set(mode)
  persistString('da.companion.responseMode', mode)
  reportCloud('companion.response_mode', mode)
}

interface BooleanPref {
  $atom: WritableAtom<boolean>
  set: (value: boolean) => void
}

function makeBooleanPref(key: string, fallback: boolean, cloudKey: string): BooleanPref {
  const $atom = atom<boolean>(storedBoolean(key, fallback))

  return {
    $atom,
    set(value: boolean): void {
      $atom.set(value)
      persistBoolean(key, value)
      reportCloud(cloudKey, value)
    }
  }
}

const llmReactionsPref = makeBooleanPref('da.companion.llmReactions', true, 'companion.llm_reactions')
const llmAffectPref = makeBooleanPref('da.companion.llmAffect', true, 'companion.llm_affect')
const llmAutonomyPref = makeBooleanPref('da.companion.llmAutonomy', true, 'companion.llm_autonomy')

const autonomousMediaPref = makeBooleanPref('da.companion.autonomousMedia', true, 'companion.autonomous_media')
const autonomousVoicePref = makeBooleanPref('da.companion.autonomousVoice', true, 'companion.autonomous_voice')

export const $autonomousMedia = autonomousMediaPref.$atom
export const $autonomousVoice = autonomousVoicePref.$atom
export const $llmReactions = llmReactionsPref.$atom
export const $llmAffect = llmAffectPref.$atom
export const $llmAutonomy = llmAutonomyPref.$atom

export { autonomousMediaPref, autonomousVoicePref, llmAffectPref, llmAutonomyPref, llmReactionsPref }

// 云端水合应用：只接受类型匹配的键，坏值静默跳过（fail-open）。
// 借道既有 setter 落 localStorage + atom；回写的 prefs:set 上报在主进程侧
// 与最近一次成功上云内容比对后消解，不会形成回环。
export function initCompanionPrefsSync(): () => void {
  const unsubscribe = window.spiritagent?.onPrefsHydrated?.(({ companion }) => {
    if (typeof companion.voice_id === 'string') {
      setCompanionVoiceId(companion.voice_id)
    }

    if (companion.response_mode === 'text' || companion.response_mode === 'voice') {
      setResponseMode(companion.response_mode)
    }

    if (typeof companion.llm_reactions === 'boolean') {
      llmReactionsPref.set(companion.llm_reactions)
    }

    if (typeof companion.llm_affect === 'boolean') {
      llmAffectPref.set(companion.llm_affect)
    }

    if (typeof companion.llm_autonomy === 'boolean') {
      llmAutonomyPref.set(companion.llm_autonomy)
    }

    if (typeof companion.autonomous_media === 'boolean') {
      autonomousMediaPref.set(companion.autonomous_media)
    }

    if (typeof companion.autonomous_voice === 'boolean') {
      autonomousVoicePref.set(companion.autonomous_voice)
    }

    // 打扰档位：只回写用户偏好（跨端恢复）；生效档位（companion.disturbance_tier）是设备
    // 派生值，供后端闸门消费，不回写本地——活动循环本地重算。
    const tier = companion.disturbance_preference

    if (tier === 'still' || tier === 'normal' || tier === 'autonomous') {
      setDisturbanceTier(tier)
    }

    // 设置面板几何（屏幕相关，取到即回写缓存；下次开面板时生效，渲染层仍做视口钳制）。
    const panel = companion.settings_panel

    if (panel != null && typeof panel === 'object') {
      const { height, offsetX, offsetY, width } = panel as Record<string, unknown>

      if (
        typeof width === 'number' &&
        typeof height === 'number' &&
        typeof offsetX === 'number' &&
        typeof offsetY === 'number'
      ) {
        localStorage.setItem('da.companion.settingsPanelSize', JSON.stringify({ height, width }))
        localStorage.setItem('da.companion.settingsPanelOffset', JSON.stringify({ dx: offsetX, dy: offsetY }))
      }
    }
  })

  return unsubscribe ?? (() => {})
}
