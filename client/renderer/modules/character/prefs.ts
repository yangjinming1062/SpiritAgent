import { atom, type WritableAtom } from 'nanostores'

import { hydrateManualReduceTransparency } from '@/shared/lib/apply-no-blur'
import {
  accountStorageKey,
  definePersistedBoolean,
  definePersistedEnum,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  registerStorageRestoreHandler,
  storedString
} from '@/shared/lib/storage'
import { preferencesApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import type { DesktopPrefsHydrated } from '@ipc/contracts'

import { setDisturbanceTier, syncDisturbanceFromStorage } from './companion-store'

// 回应偏好同步到后端供模型选择消息类型，不控制客户端合成或播放。
export type ResponsePreference = 'text' | 'voice'

const COMPANION_VOICE_ID_STORAGE_KEY = registerCompanionStorageKey('da.companion.voiceId')
const RESPONSE_PREFERENCE_STORAGE_KEY = 'da.companion.responsePreference'
const AUTOPLAY_VOICE_STORAGE_KEY = 'da.companion.autoplayVoice'
const POSTS_ENABLED_STORAGE_KEY = 'da.companion.postsEnabled'
const INFORMATION_DISCOVERY_STORAGE_KEY = 'da.companion.informationDiscoveryEnabled'
const LLM_AFFECT_STORAGE_KEY = 'da.companion.llmAffect'
const LLM_AUTONOMY_STORAGE_KEY = 'da.companion.llmAutonomy'

// localStorage 是各窗口的即时缓存；本地编辑经 prefs:set 合入主进程镜像并上云。服务端确认的音色先同步镜像，再由水合广播回写缓存与 atom。
function reportCloud(key: string, value: unknown): void {
  preferencesApi()?.prefs?.set({ key, value })
}

export const $companionVoiceId = atom<string>(storedString(COMPANION_VOICE_ID_STORAGE_KEY) ?? '')

const responsePreferencePersisted = definePersistedEnum<ResponsePreference>({
  allowed: ['text', 'voice'],
  fallback: 'text',
  key: RESPONSE_PREFERENCE_STORAGE_KEY
})

export const $responsePreference = responsePreferencePersisted.$atom

export function setCompanionVoiceId(voice: string): void {
  cacheCompanionVoiceId(voice)
  reportCloud('companion.voice_id', voice)
}

function cacheCompanionVoiceId(voice: string): void {
  $companionVoiceId.set(voice)
  persistString(COMPANION_VOICE_ID_STORAGE_KEY, voice || null)
}

export async function applyServerCompanionVoiceId(voice: string): Promise<void> {
  const auth = $auth.get()

  if (auth.kind !== 'authenticated') {
    return
  }

  await preferencesApi().prefs.applyServerVoice({
    accountId: auth.snapshot.accountId,
    authSessionId: auth.snapshot.sessionId,
    voiceId: voice
  })
}

export function setResponsePreference(mode: ResponsePreference): void {
  responsePreferencePersisted.set(mode)
  reportCloud('companion.response_preference', mode)
}

registerStorageClearHandler(() => {
  $companionVoiceId.set('')
})

registerStorageRestoreHandler(() => {
  $companionVoiceId.set(storedString(COMPANION_VOICE_ID_STORAGE_KEY) ?? '')
})

interface BooleanPref {
  $atom: WritableAtom<boolean>
  reload: () => void
  set: (value: boolean) => void
}

function makeBooleanPref(key: string, fallback: boolean, cloudKey: string): BooleanPref {
  const persisted = definePersistedBoolean({ key, fallback })

  return {
    $atom: persisted.$atom,
    reload: persisted.reload,
    set(value: boolean): void {
      persisted.set(value)
      reportCloud(cloudKey, value)
    }
  }
}

const llmAffectPref = makeBooleanPref(LLM_AFFECT_STORAGE_KEY, true, 'companion.llm_affect')
const llmAutonomyPref = makeBooleanPref(LLM_AUTONOMY_STORAGE_KEY, true, 'companion.llm_autonomy')

const postsEnabledPref = makeBooleanPref(POSTS_ENABLED_STORAGE_KEY, true, 'companion.posts_enabled')
export const $postsEnabled = postsEnabledPref.$atom
export { postsEnabledPref }

export const informationDiscoveryPref = makeBooleanPref(
  INFORMATION_DISCOVERY_STORAGE_KEY,
  false,
  'companion.information_discovery_enabled'
)
export const $informationDiscoveryEnabled = informationDiscoveryPref.$atom

export const autoplayVoicePref = makeBooleanPref(AUTOPLAY_VOICE_STORAGE_KEY, true, 'companion.autoplay_voice')
export const $autoplayVoice = autoplayVoicePref.$atom

export const $llmAffect = llmAffectPref.$atom
export const $llmAutonomy = llmAutonomyPref.$atom

export { llmAffectPref, llmAutonomyPref }

// 云端水合时按序回写的布尔偏好（companion 节键 → 偏好）。
const HYDRATED_BOOLEAN_PREFS = [
  ['posts_enabled', postsEnabledPref],
  ['information_discovery_enabled', informationDiscoveryPref],
  ['llm_affect', llmAffectPref],
  ['llm_autonomy', llmAutonomyPref]
] as const

// 云端水合应用：只接受类型匹配的键；音色仅回写缓存，其他偏好沿用 setter，主进程消解与已上云内容相同的回写。
export function initCompanionPrefsSync(): () => void {
  const onStorage = (event: StorageEvent): void => {
    if (event.key === accountStorageKey(COMPANION_VOICE_ID_STORAGE_KEY) || event.key === null) {
      $companionVoiceId.set(storedString(COMPANION_VOICE_ID_STORAGE_KEY) ?? '')
    }

    if (event.key === accountStorageKey(AUTOPLAY_VOICE_STORAGE_KEY) || event.key === null) {
      autoplayVoicePref.reload()
    }

    if (event.key === accountStorageKey(RESPONSE_PREFERENCE_STORAGE_KEY) || event.key === null) {
      responsePreferencePersisted.reload()
    }

    if (event.key === accountStorageKey(POSTS_ENABLED_STORAGE_KEY) || event.key === null) {
      postsEnabledPref.reload()
    }

    if (event.key === accountStorageKey(INFORMATION_DISCOVERY_STORAGE_KEY) || event.key === null) {
      informationDiscoveryPref.reload()
    }

    if (event.key === accountStorageKey(LLM_AFFECT_STORAGE_KEY) || event.key === null) {
      llmAffectPref.reload()
    }

    if (event.key === accountStorageKey(LLM_AUTONOMY_STORAGE_KEY) || event.key === null) {
      llmAutonomyPref.reload()
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
      cacheCompanionVoiceId(companion.voice_id)
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

  const unsubscribe = preferencesApi()?.onPrefsHydrated?.(payload => {
    pending = payload
    applyHydrated()
  })

  return () => {
    unsubscribeAuth()
    unsubscribe?.()
    window.removeEventListener('storage', onStorage)
  }
}
