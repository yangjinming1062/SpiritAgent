import { type OnboardingAnswers, SPEAKING_STYLE_PRESETS, SPECIES_PRESETS, VOICE_PRESETS } from '@/features/character'

import type { Phase } from './back-transition'

export type QKey = keyof OnboardingAnswers

export interface Question {
  key: 'name' | 'biological_type' | 'speaking_style' | 'voice'
  multiline?: boolean
  presets?: readonly string[]
  max: number
}

export const CHARACTER_QUESTIONS: readonly Question[] = [
  { key: 'name', max: 500 },
  { key: 'biological_type', max: 500, presets: SPECIES_PRESETS },
  { key: 'speaking_style', max: 500, multiline: true, presets: SPEAKING_STYLE_PRESETS }
]

export const VOICE_QUESTION: Question = { key: 'voice', max: 500, presets: VOICE_PRESETS }

interface ResumeTarget {
  phase: Phase
  qIndex?: number
  imageSealed?: boolean
  voiceStage?: 'catalog'
}

export function resolveResumeTarget(nextField: string | null | undefined): ResumeTarget | null {
  switch (nextField) {
    case 'background':
      return { phase: 'background' }

    case 'portrait':
      return { phase: 'portrait-generate' }

    case 'fullbody-reference':
      return { phase: 'fullbody-reference' }

    case 'voice':
      return { phase: 'voice', voiceStage: 'catalog', qIndex: 0, imageSealed: true }
    default: {
      const qIndex = CHARACTER_QUESTIONS.findIndex(question => question.key === nextField)

      return qIndex < 0 ? null : { phase: 'q-character', qIndex }
    }
  }
}

// 本地非空编辑优先，服务端草稿只补未填写项。
export function mergeResumeAnswers(local: OnboardingAnswers, saved: Record<string, string>): OnboardingAnswers {
  const merged = { ...local }

  const fields: readonly QKey[] = [
    'name',
    'biological_type',
    'speaking_style',
    'gender',
    'personality',
    'relationship',
    'age',
    'occupation',
    'background',
    'voice'
  ]

  for (const key of fields) {
    if (!merged[key] && saved[key] !== undefined) {
      merged[key] = saved[key]
    }
  }

  return merged
}
