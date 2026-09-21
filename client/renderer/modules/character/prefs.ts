import { atom, onMount, type WritableAtom } from 'nanostores'

import { hydrateManualReduceTransparency } from '@/shared/lib/apply-no-blur'
import {
  persistBoolean,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedBoolean,
  storedString
} from '@/shared/lib/storage'

import { setDisturbanceTier } from './companion-store'

// 回应偏好同步到后端供模型选择消息类型，不控制客户端合成或播放。
export type ResponsePreference = 'text' | 'voice'

const COMPANION_VOICE_ID_STORAGE_KEY = registerCompanionStorageKey('da.companion.voiceId')
const RESPONSE_PREFERENCE_STORAGE_KEY = registerCompanionStorageKey('da.companion.responsePreference')

// localStorage 仍是各窗口的即时缓存（同步读、离线可用）；每次写入额外经
// prefs:set 通道上报主进程，并入 companion.* 云同步节（云端真源，PROTOCOL §2.4）。
// 水合广播（initCompanionPrefsSync）用云端值回写缓存与 atom，跨端收敛。
function reportCloud(key: string, value: unknown): void {
  window.spiritagent?.prefs?.set({ key, value })
}

export const $companionVoiceId = atom<string>(storedString(COMPANION_VOICE_ID_STORAGE_KEY) ?? '')
export const $responsePreference = atom<ResponsePreference>(
  storedString(RESPONSE_PREFERENCE_STORAGE_KEY) === 'voice' ? 'voice' : 'text'
)

// 音色与回应偏好在生活空间设置，轻语共用同一组云端偏好。
// 各窗口内存独立，借 storage 事件把其他窗口的写入热同步进 atom。
onMount($companionVoiceId, () => {
  const refresh = (event: StorageEvent): void => {
    if (event.key === COMPANION_VOICE_ID_STORAGE_KEY) {
      $companionVoiceId.set(event.newValue ?? '')
    }
  }

  window.addEventListener('storage', refresh)

  return () => window.removeEventListener('storage', refresh)
})

export function setCompanionVoiceId(voice: string): void {
  $companionVoiceId.set(voice)
  persistString(COMPANION_VOICE_ID_STORAGE_KEY, voice || null)
  reportCloud('companion.voice_id', voice)
}

export function setResponsePreference(mode: ResponsePreference): void {
  $responsePreference.set(mode)
  persistString(RESPONSE_PREFERENCE_STORAGE_KEY, mode)
  reportCloud('companion.response_preference', mode)
}

registerStorageClearHandler(() => {
  $companionVoiceId.set('')
  $responsePreference.set('text')
})

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

const llmAffectPref = makeBooleanPref('da.companion.llmAffect', true, 'companion.llm_affect')
const llmAutonomyPref = makeBooleanPref('da.companion.llmAutonomy', true, 'companion.llm_autonomy')

const autonomousMediaPref = makeBooleanPref('da.companion.autonomousMedia', true, 'companion.autonomous_media')
const autonomousVoicePref = makeBooleanPref('da.companion.autonomousVoice', true, 'companion.autonomous_voice')

export const $autonomousMedia = autonomousMediaPref.$atom
export const $autonomousVoice = autonomousVoicePref.$atom
export const $llmAffect = llmAffectPref.$atom
export const $llmAutonomy = llmAutonomyPref.$atom

export { autonomousMediaPref, autonomousVoicePref, llmAffectPref, llmAutonomyPref }

// 云端水合应用：只接受类型匹配的键，坏值静默跳过（fail-open）。
// 借道既有 setter 落 localStorage + atom；回写的 prefs:set 上报在主进程侧
// 与最近一次成功上云内容比对后消解，不会形成回环。
export function initCompanionPrefsSync(): () => void {
  // 发送消息直接读取偏好；监听生命周期不能依赖设置面板是否订阅 atom。
  const refreshResponsePreference = (): void => {
    $responsePreference.set(storedString(RESPONSE_PREFERENCE_STORAGE_KEY) === 'voice' ? 'voice' : 'text')
  }

  const onStorage = (event: StorageEvent): void => {
    if (event.key === RESPONSE_PREFERENCE_STORAGE_KEY || event.key === null) {
      refreshResponsePreference()
    }
  }

  refreshResponsePreference()
  window.addEventListener('storage', onStorage)

  const unsubscribe = window.spiritagent?.onPrefsHydrated?.(({ companion }) => {
    if (typeof companion.voice_id === 'string') {
      setCompanionVoiceId(companion.voice_id)
    }

    if (companion.response_preference === 'text' || companion.response_preference === 'voice') {
      setResponsePreference(companion.response_preference)
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

    // 减少透明效果（玻璃降级手动开关）：跨窗口、跨端经 companion 节同步。
    if (typeof companion.reduce_transparency === 'boolean') {
      hydrateManualReduceTransparency(companion.reduce_transparency)
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

  return () => {
    unsubscribe?.()
    window.removeEventListener('storage', onStorage)
  }
}
