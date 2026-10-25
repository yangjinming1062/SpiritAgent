import type { ApiSchemas } from '../../../shared/api.generated'

export type VoiceOption = ApiSchemas['VoiceEntry']

// 声线筛选的合法 gender 值与目录数据（v.gender）对齐；展示文案由页面按 locale 取词。
export const GENDER_FILTER_VALUES = ['', 'female', 'male', 'neutral'] as const

export function voiceSelectionId(voice: Pick<VoiceOption, 'id' | 'provider'>): string {
  return `${voice.provider}:${voice.id}`
}
