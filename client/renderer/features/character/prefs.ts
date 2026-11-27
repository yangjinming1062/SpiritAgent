import { atom } from 'nanostores'

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

// 动态表现强度与打扰档位独立；subtle 限制自动选择的强度，用户明确触发的反应仍可播放。
export type MotionLevel = 'off' | 'subtle' | 'standard'

const MOTION_LEVEL_STORAGE_KEY = 'da.companion.motionLevel'
const MOTION_LEVELS: readonly MotionLevel[] = ['off', 'subtle', 'standard']

const motionLevelPersisted = definePersistedEnum<MotionLevel>({
  allowed: MOTION_LEVELS,
  fallback: 'standard',
  key: MOTION_LEVEL_STORAGE_KEY
})

export const $motionLevel = motionLevelPersisted.$atom

export function setMotionLevel(level: MotionLevel): void {
  motionLevelPersisted.set(level)
  reportCloud('companion.motion_level', level)
}

function reloadMotionLevel(): void {
  motionLevelPersisted.reload()
}

const COMPANION_VOICE_ID_STORAGE_KEY = registerCompanionStorageKey('da.companion.voiceId')
const RESPONSE_PREFERENCE_STORAGE_KEY = 'da.companion.responsePreference'
const AUTOPLAY_VOICE_STORAGE_KEY = 'da.companion.autoplayVoice'

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

const autoplayVoice = definePersistedBoolean({ key: AUTOPLAY_VOICE_STORAGE_KEY, fallback: true })
export const $autoplayVoice = autoplayVoice.$atom

export function setAutoplayVoice(value: boolean): void {
  autoplayVoice.set(value)
  reportCloud('companion.autoplay_voice', value)
}

// 云端水合应用：只接受类型匹配的键；音色仅回写缓存，其他偏好沿用 setter，主进程消解与已上云内容相同的回写。
export function initCompanionPrefsSync(): () => void {
  const onStorage = (event: StorageEvent): void => {
    if (event.key === accountStorageKey(COMPANION_VOICE_ID_STORAGE_KEY) || event.key === null) {
      $companionVoiceId.set(storedString(COMPANION_VOICE_ID_STORAGE_KEY) ?? '')
    }

    if (event.key === accountStorageKey(AUTOPLAY_VOICE_STORAGE_KEY) || event.key === null) {
      autoplayVoice.reload()
    }

    if (event.key === accountStorageKey(RESPONSE_PREFERENCE_STORAGE_KEY) || event.key === null) {
      responsePreferencePersisted.reload()
    }

    if (event.key === accountStorageKey(MOTION_LEVEL_STORAGE_KEY) || event.key === null) {
      reloadMotionLevel()
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
      setAutoplayVoice(companion.autoplay_voice)
    }

    if (typeof companion.voice_id === 'string') {
      cacheCompanionVoiceId(companion.voice_id)
    }

    if (companion.response_preference === 'text' || companion.response_preference === 'voice') {
      setResponsePreference(companion.response_preference)
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

    // 动态表现强度与档位独立同步。
    const motion = companion.motion_level

    if (motion === 'off' || motion === 'subtle' || motion === 'standard') {
      motionLevelPersisted.set(motion)
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
