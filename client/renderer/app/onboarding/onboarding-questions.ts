import {
  CHARACTER_GENDER_PRESETS,
  MAX_USER_TEXT,
  type OnboardingAnswers,
  PERSONALITY_PRESETS,
  RELATIONSHIP_PRESETS,
  SPEAKING_STYLE_PRESETS,
  SPECIES_PRESETS,
  USER_GENDER_PRESETS,
  VOICE_PRESETS
} from '@/modules/character'

import type { Phase } from './back-transition'
import type { OnboardingAudioTag } from './onboarding-audio'

// 全部字段都经 onboarding PATCH 提交，与后端 ONBOARDING_FIELDS 一致。
export type QKey = keyof OnboardingAnswers

// chip 选的是答案类别而非答案本身——见 CALL_NAME_KINDS。
export interface AnswerKind {
  chip: string
  label: string
  placeholder: string
  values?: readonly string[]
}

export interface Question {
  key: QKey
  text: string
  placeholder: string
  // 有值即必填：作答为空时显示该提示，且不可跳过。
  requiredHint?: string
  multiline?: boolean
  // Manifest tag 与录到的语音行绑定而非位置绑定——重排 QUESTIONS 也不会让音频错位。
  audioTag: OnboardingAudioTag
  presets?: readonly string[]
  max?: number
  // 与 `presets` 互斥：双层入口，而不是「点 chip 就把输入框填好」。
  kinds?: readonly AnswerKind[]
  date?: boolean
}

// 「名字/昵称」是称呼类别不是称呼值，点 chip 只换标签再问具体值；「称号」额外给现成选项。
const CALL_NAME_KINDS: readonly AnswerKind[] = [
  { chip: '名字', label: '那，您的名字是？', placeholder: '比如：张三' },
  { chip: '昵称', label: '那，您的昵称是？', placeholder: '比如：小明、阿棠' },
  {
    chip: '称号',
    label: '想让我用哪个称号？',
    placeholder: '或者自己写一个…',
    values: ['老板', '主人', '老师', '大人']
  },
  { chip: '自填', label: '那，想让我怎么叫您？', placeholder: '随便写，我记住就是了…' }
]

export const QUESTIONS: readonly Question[] = [
  {
    key: 'name',
    text: '您好…我还不认识自己。您愿意给我一个名字吗？',
    placeholder: '给我起个名字吧',
    requiredHint: '名字是必填的哦～',
    audioTag: 'onboarding.q0'
  },
  {
    key: 'biological_type',
    text: '那我是哪种生灵呢？',
    placeholder: '或者自由描述…',
    requiredHint: '生灵类型是必填的哦～',
    audioTag: 'onboarding.q1',
    presets: SPECIES_PRESETS
  },
  {
    key: 'gender',
    text: '嗯…那我是男性、女性、还是…',
    placeholder: '或者自由描述…',
    audioTag: 'onboarding.q2',
    presets: CHARACTER_GENDER_PRESETS
  },
  {
    key: 'relationship',
    text: '好的，那您希望我是什么样的身份？',
    placeholder: '或者自由描述…',
    audioTag: 'onboarding.q4',
    presets: RELATIONSHIP_PRESETS
  },
  {
    key: 'personality',
    text: '您希望我是什么性格？',
    placeholder: '自由描述…',
    audioTag: 'onboarding.q5',
    presets: PERSONALITY_PRESETS
  },
  // speaking_style 是后端 schema 必填项，用专门一道题收集，与其它角色字段一起进 enterPortraitStage 的 PUT。
  {
    key: 'speaking_style',
    text: '您希望我说话的风格是什么样的？',
    placeholder: '比如：简短、爱用比喻、俏皮一点…',
    requiredHint: '说话风格是必填的哦～',
    multiline: true,
    audioTag: 'onboarding.q10',
    max: 500,
    presets: SPEAKING_STYLE_PRESETS
  },
  {
    key: 'voice',
    text: '您希望我听起来是什么样的？比如温柔的少女音、沉稳的男声、活泼的正太…',
    placeholder: '描述你想要的声音…',
    audioTag: 'onboarding.q12',
    presets: VOICE_PRESETS
  },
  {
    key: 'user_call_name',
    text: '我该怎么称呼您？',
    placeholder: '或者自由描述…',
    audioTag: 'onboarding.q6',
    max: MAX_USER_TEXT,
    kinds: CALL_NAME_KINDS
  },
  {
    key: 'user_gender',
    text: '您方便告诉我您的性别吗？',
    placeholder: '或自由描述…',
    audioTag: 'onboarding.q7',
    max: MAX_USER_TEXT,
    presets: USER_GENDER_PRESETS
  },
  {
    key: 'user_birthday',
    text: '您方便告诉我您的生日吗？',
    placeholder: '选择日期（可不填）',
    audioTag: 'onboarding.q8',
    max: MAX_USER_TEXT,
    date: true
  },
  {
    key: 'user_hobbies',
    text: '您平时喜欢什么？',
    placeholder: '可以多写几个…',
    multiline: true,
    audioTag: 'onboarding.q9',
    max: MAX_USER_TEXT
  },
  {
    key: 'user_freeform',
    text: '还有什么想告诉我、或者想叮嘱我的吗？',
    placeholder: '可跳过…',
    multiline: true,
    audioTag: 'onboarding.q11',
    max: MAX_USER_TEXT
  }
]

// 确认全身形象后固定；外形细节由角色卡维护。
export const LOCKED_FIELD_LABELS: Partial<Record<QKey, string>> = {
  biological_type: '物种',
  gender: '性别'
}

// 分段边界由 voice 题位置决定：之前是角色子阶段，它是声音子阶段，之后是用户子阶段（对应后端 ONBOARDING_FIELDS 顺序）。
const VOICE_Q_INDEX = QUESTIONS.findIndex(q => q.key === 'voice')
export const CHARACTER_QUESTIONS: readonly Question[] = QUESTIONS.slice(0, VOICE_Q_INDEX)
export const VOICE_QUESTIONS: readonly Question[] = QUESTIONS.slice(VOICE_Q_INDEX, VOICE_Q_INDEX + 1)
export const USER_QUESTIONS: readonly Question[] = QUESTIONS.slice(VOICE_Q_INDEX + 1)

export const PHASE_QUESTIONS: Record<Phase, readonly Question[]> = {
  'q-character': CHARACTER_QUESTIONS,
  'q-user': USER_QUESTIONS,
  voice: VOICE_QUESTIONS,
  'portrait-generate': [],
  hatching: [],
  'portrait-avatar': [],
  'fullbody-reference': [],
  finishing: []
}

// resume 的 next_field 路由到 q-user（voice 另有分支）；从 USER_QUESTIONS 推导，题目增删自动同步。
export const POST_CHARACTER_FIELDS: ReadonlySet<string> = new Set(USER_QUESTIONS.map(q => q.key))

export interface ResumeTarget {
  phase: Phase
  qIndex?: number
  imageSealed?: boolean
  voiceStage?: 'catalog'
}

export function resolveResumeTarget(nextField: string | null | undefined): ResumeTarget | null {
  if (!nextField) {
    return null
  }

  if (nextField === 'portrait') {
    return { phase: 'portrait-generate' }
  }

  if (nextField === 'fullbody-reference') {
    return { phase: 'fullbody-reference' }
  }

  if (nextField === 'voice') {
    return { phase: 'voice', voiceStage: 'catalog', qIndex: 0, imageSealed: true }
  }

  const isUserField = POST_CHARACTER_FIELDS.has(nextField)
  const questions = isUserField ? USER_QUESTIONS : CHARACTER_QUESTIONS

  return {
    phase: isUserField ? 'q-user' : 'q-character',
    qIndex: Math.max(
      0,
      questions.findIndex(question => question.key === nextField)
    ),
    ...(isUserField && { imageSealed: true })
  }
}

// 本地非空编辑优先，服务端草稿只补未填写项。
export function mergeResumeAnswers(local: OnboardingAnswers, saved: Record<string, string>): OnboardingAnswers {
  const merged = { ...local }

  for (const question of QUESTIONS) {
    if ((merged[question.key] == null || merged[question.key] === '') && saved[question.key] !== undefined) {
      merged[question.key] = saved[question.key]
    }
  }

  return merged
}
