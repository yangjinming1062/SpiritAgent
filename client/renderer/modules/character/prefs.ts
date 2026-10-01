import type { DesktopPrefsHydrated } from '@ipc/contracts'
import { atom, onMount, type WritableAtom } from 'nanostores'

import { hydrateManualReduceTransparency } from '@/shared/lib/apply-no-blur'
import {
  accountStorageKey,
  definePersistedEnum,
  persistBoolean,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  registerStorageRestoreHandler,
  storedBoolean,
  storedString
} from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import { setDisturbanceTier, syncDisturbanceFromStorage } from './companion-store'

// 回应偏好同步到后端供模型选择消息类型，不控制客户端合成或播放。
export type ResponsePreference = 'text' | 'voice'

const COMPANION_VOICE_ID_STORAGE_KEY = registerCompanionStorageKey('da.companion.voiceId')
const RESPONSE_PREFERENCE_STORAGE_KEY = 'da.companion.responsePreference'
const AUTOPLAY_VOICE_STORAGE_KEY = registerCompanionStorageKey('da.companion.autoplayVoice')
const POSTS_ENABLED_STORAGE_KEY = registerCompanionStorageKey('da.companion.postsEnabled')

// localStorage 仍是各窗口的即时缓存（同步读、离线可用）；每次写入额外经 prefs:set 通道上报主进程，并入 companion.* 云同步节（云端真源，PROTOCOL「配置所有权与云同步」）。水合广播（initCompanionPrefsSync）用云端值回写缓存与 atom，跨端收敛。
function reportCloud(key: string, value: unknown): void {
  window.spiritagent?.prefs?.set({ key, value })
}

export const $companionVoiceId = atom<string>(storedString(COMPANION_VOICE_ID_STORAGE_KEY) ?? '')

const responsePreferencePersisted = definePersistedEnum<ResponsePreference>({
  allowed: ['text', 'voice'],
  fallback: 'text',
  key: RESPONSE_PREFERENCE_STORAGE_KEY
})

export const $responsePreference = responsePreferencePersisted.$atom

// 音色与回应偏好在生活空间设置，轻语共用同一组云端偏好。各窗口内存独立，借 storage 事件把其他窗口的写入热同步进 atom。
onMount($companionVoiceId, () => {
  const refresh = (event: StorageEvent): void => {
    if (event.key === accountStorageKey(COMPANION_VOICE_ID_STORAGE_KEY)) {
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
  responsePreferencePersisted.set(mode)
  reportCloud('companion.response_preference', mode)
}

registerStorageClearHandler(() => {
  $companionVoiceId.set('')
  $autoplayVoice.set(true)
  $postsEnabled.set(true)
})

registerStorageRestoreHandler(() => {
  $companionVoiceId.set(storedString(COMPANION_VOICE_ID_STORAGE_KEY) ?? '')
  $autoplayVoice.set(storedBoolean(AUTOPLAY_VOICE_STORAGE_KEY, true))
  $postsEnabled.set(storedBoolean(POSTS_ENABLED_STORAGE_KEY, true))
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

const postsEnabledPref = makeBooleanPref(POSTS_ENABLED_STORAGE_KEY, true, 'companion.posts_enabled')
export const $postsEnabled = postsEnabledPref.$atom
export { postsEnabledPref }

export const autoplayVoicePref = makeBooleanPref(AUTOPLAY_VOICE_STORAGE_KEY, true, 'companion.autoplay_voice')
export const $autoplayVoice = autoplayVoicePref.$atom

export const $llmAffect = llmAffectPref.$atom
export const $llmAutonomy = llmAutonomyPref.$atom

export { llmAffectPref, llmAutonomyPref }

// 云端水合时按序回写的布尔偏好（companion 节键 → 偏好）。
const HYDRATED_BOOLEAN_PREFS = [
  ['posts_enabled', postsEnabledPref],
  ['llm_affect', llmAffectPref],
  ['llm_autonomy', llmAutonomyPref]
] as const

// 云端水合应用：只接受类型匹配的键，坏值静默跳过（fail-open）。借道既有 setter 落 localStorage + atom；回写的 prefs:set 上报在主进程侧与最近一次成功上云内容比对后消解，不会形成回环。
export function initCompanionPrefsSync(): () => void {
  const onStorage = (event: StorageEvent): void => {
    if (event.key === accountStorageKey(AUTOPLAY_VOICE_STORAGE_KEY) || event.key === null) {
      $autoplayVoice.set(storedBoolean(AUTOPLAY_VOICE_STORAGE_KEY, true))
    }

    if (event.key === accountStorageKey(RESPONSE_PREFERENCE_STORAGE_KEY) || event.key === null) {
      responsePreferencePersisted.reload()
    }

    if (event.key === accountStorageKey(POSTS_ENABLED_STORAGE_KEY) || event.key === null) {
      $postsEnabled.set(storedBoolean(POSTS_ENABLED_STORAGE_KEY, true))
    }

    syncDisturbanceFromStorage(event.key)
  }

  // 发送消息直接读取偏好；监听生命周期不能依赖设置面板是否订阅 atom。
  responsePreferencePersisted.reload()
  window.addEventListener('storage', onStorage)

  let pending: DesktopPrefsHydrated | null = null

  const applyHydrated = (): void => {
    const auth = $auth.get()

    if (!pending || auth.kind !== 'authenticated' || pending.accountId !== auth.snapshot.accountId) {
      return
    }

    const { companion } = pending
    pending = null

    if (typeof companion.autoplay_voice === 'boolean') {
      autoplayVoicePref.set(companion.autoplay_voice)
    }

    if (typeof companion.voice_id === 'string') {
      setCompanionVoiceId(companion.voice_id)
    }

    if (companion.response_preference === 'text' || companion.response_preference === 'voice') {
      setResponsePreference(companion.response_preference)
    }

    for (const [key, pref] of HYDRATED_BOOLEAN_PREFS) {
      const value = companion[key]

      if (typeof value === 'boolean') {
        pref.set(value)
      }
    }

    // 减少透明效果（玻璃降级手动开关）：跨窗口、跨端经 companion 节同步。
    if (typeof companion.reduce_transparency === 'boolean') {
      hydrateManualReduceTransparency(companion.reduce_transparency)
    }

    // 打扰档位：只回写用户偏好（跨端恢复，不结束本机临时安静）；生效档位（companion.disturbance_tier）是设备派生值，供后端闸门消费，不回写本地——活动循环本地重算。
    const tier = companion.disturbance_preference

    if (tier === 'still' || tier === 'normal' || tier === 'autonomous') {
      setDisturbanceTier(tier)
    }
  }

  // 配置广播可先于鉴权广播到达；等目标账户的本地存储切换完成再应用。
  const unsubscribeAuth = $auth.listen(applyHydrated)

  const unsubscribe = window.spiritagent?.onPrefsHydrated?.(payload => {
    pending = payload
    applyHydrated()
  })

  return () => {
    unsubscribeAuth()
    unsubscribe?.()
    window.removeEventListener('storage', onStorage)
  }
}
