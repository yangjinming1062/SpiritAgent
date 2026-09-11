import type { SpeechStyle } from '@ipc/contracts'
import { atom } from 'nanostores'

// 语音条在会话侧的状态投影与控制接缝。播放队列的真相在 modules/speech，
// 消息中的语音状态只是 UI 投影——会话模块不导入语音模块，由
// app/workflows/conversation-speech 在启动时绑定实现（窄接口、显式装配）。

export const $voiceBarPlayingId = atom<string | null>(null)
export const $voiceBarLoadingId = atom<string | null>(null)

export function setVoiceBarPlaying(id: string | null): void {
  $voiceBarPlayingId.set(id)
}

export function setVoiceBarLoading(id: string | null): void {
  $voiceBarLoadingId.set(id)
}

export interface ConversationVoiceSink {
  /** 生活空间「始终语音」是否生效（代理窗 + voice 模式 + 未锁屏）。 */
  isActive(): boolean
  cachedDuration(text: string, speechStyle?: SpeechStyle): number | undefined
  /** 收尾的助手消息请求合成；是否自动播放由语音侧按窗口状态裁决。 */
  synthesize(messageId: string, text: string): void
  /** 切换会话、中止回合、重置与登出时终止全部语音活动。 */
  cancel(): void
  estimateDuration(text: string): number
}

let voiceSink: ConversationVoiceSink | null = null

export function setConversationVoiceSink(sink: ConversationVoiceSink): void {
  voiceSink = sink
}

export function conversationVoiceSink(): ConversationVoiceSink {
  if (!voiceSink) {
    throw new Error('conversation voice sink not bound — app bootstrap must initialize first')
  }

  return voiceSink
}

export interface VoiceBarControl {
  /** 点击语音条：播放中则停止，否则合成/取缓存并播放。 */
  toggle(messageId: string): void
  /** 语音条缺时长时的按需解析，只投影时长、不进入播放队列。 */
  ensureDuration(messageId: string, text: string): void
}

let voiceControl: VoiceBarControl | null = null

export function setVoiceBarControl(control: VoiceBarControl): void {
  voiceControl = control
}

export function voiceBarControl(): VoiceBarControl {
  if (!voiceControl) {
    throw new Error('voice bar control not bound — app bootstrap must initialize first')
  }

  return voiceControl
}
