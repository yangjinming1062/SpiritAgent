export interface VoiceOption {
  id: string
  provider: string
  label: string
  gender: string
  language: string
  tags: readonly string[]
  description: string
  // 供应商提供的设计音色说明，展示在枢纽层的画廊中。
  voice_design_guide?: string
}

// 声线筛选的合法 gender 值与目录数据（v.gender）对齐；展示文案由页面按 locale 取词。
export const GENDER_FILTER_VALUES = ['', 'female', 'male', 'neutral'] as const

const PROVIDER_LABELS: Record<string, string> = {
  grok: 'xAI',
  minimax: 'MiniMax',
  mimo: 'MiMo'
}

export function voiceProviderLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider
}

export function voiceSelectionId(voice: Pick<VoiceOption, 'id' | 'provider'>): string {
  return `${voice.provider}:${voice.id}`
}

export function customVoiceSelectionId(provider: string, voiceId: string): string {
  return `${provider}:custom:${voiceId}`
}

export function isCustomVoiceSelectionId(value: string): boolean {
  return value.split(':', 3)[1] === 'custom'
}

export function voiceSelectionProvider(value: string): string {
  return value.split(':', 1)[0] ?? ''
}

export type RequestGateway = <T>(method: string, params?: Record<string, unknown>) => Promise<T>
