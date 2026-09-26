export interface OnboardingAnswers {
  name?: string
  biological_type?: string
  gender?: string
  // relationship：用户与精灵的关系（知己好友 / 赛博管家等）。只进入对话系统提示词，
  // 不参与性格标签分析与形象生成——关系是两者之间的，不是精灵自身的属性。
  relationship?: string
  personality?: string
  speaking_style?: string
  user_call_name?: string
  user_gender?: string
  user_birthday?: string
  user_hobbies?: string
  user_freeform?: string
  voice?: string
}

export interface PersonaPayload {
  name: string
  personality: string
  speaking_style: string
  relationship?: string
  biological_type?: string
  gender?: string
  user_call_name?: string
  user_gender?: string
  user_birthday?: string
  user_hobbies?: string
  user_freeform?: string
}

const DEFAULT_PERSONALITY = '温柔体贴'
export const MAX_IMAGE_DESCRIPTION = 500
export const MAX_USER_TEXT = 2000
const MAX_SPECIES_GENDER = 64
const MAX_RELATIONSHIP = 500

function truncate(value: string | undefined, max: number): string | undefined {
  if (!value) {
    return undefined
  }

  const trimmed = value.trim()

  if (!trimmed) {
    return undefined
  }

  return trimmed.slice(0, max)
}

export function assemblePersona(answers: OnboardingAnswers, previous?: Partial<PersonaPayload> | null): PersonaPayload {
  const name = answers.name?.trim() || '伙伴'
  const personality = answers.personality?.trim() || DEFAULT_PERSONALITY

  // 客户端只透传用户输入，不做程序化转换；未填写时不注入默认性格
  const userPickedStyle = answers.speaking_style?.trim()
  const speakingStyle = userPickedStyle || previous?.speaking_style?.trim() || ''

  const payload: PersonaPayload = {
    name,
    personality,
    speaking_style: speakingStyle
  }

  // 锁定的视觉锚点字段在用户没填时回退到 `previous`——后端 PUT /persona
  // 做整体替换，不带回就会把它们清掉。
  const prev = previous ?? {}

  const optional: Array<[keyof PersonaPayload, string | undefined, number]> = [
    ['biological_type', answers.biological_type ?? prev.biological_type, MAX_SPECIES_GENDER],
    ['gender', answers.gender ?? prev.gender, MAX_SPECIES_GENDER],
    ['relationship', answers.relationship ?? prev.relationship, MAX_RELATIONSHIP],
    ['user_call_name', answers.user_call_name ?? prev.user_call_name, MAX_USER_TEXT],
    ['user_gender', answers.user_gender ?? prev.user_gender, MAX_USER_TEXT],
    ['user_birthday', answers.user_birthday ?? prev.user_birthday, MAX_USER_TEXT],
    ['user_hobbies', answers.user_hobbies ?? prev.user_hobbies, MAX_USER_TEXT],
    ['user_freeform', answers.user_freeform ?? prev.user_freeform, MAX_USER_TEXT]
  ]

  for (const [key, raw, max] of optional) {
    const trimmed = truncate(raw, max)

    if (trimmed) {
      payload[key] = trimmed
    }
  }

  return payload
}

// assemblePersona 的「仅角色」子集——剥掉 user_*，让 enterHatching 在 q-user / voice
// 还没收集时就能完成角色定型。
export function assembleCharacterPersona(answers: OnboardingAnswers): PersonaPayload {
  const payload = assemblePersona(answers)

  delete payload.user_call_name
  delete payload.user_gender
  delete payload.user_birthday
  delete payload.user_hobbies
  delete payload.user_freeform

  return payload
}
