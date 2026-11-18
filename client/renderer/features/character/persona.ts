export interface OnboardingAnswers {
  name?: string
  biological_type?: string
  gender?: string
  // relationship：用户与精灵的关系（知己好友 / 赛博管家等）。只进入对话系统提示词，不参与性格标签分析与形象生成——关系是两者之间的，不是精灵自身的属性。
  relationship?: string
  personality?: string
  speaking_style?: string
  age?: string
  occupation?: string
  background?: string
  voice?: string
  voice_setup_skipped?: string
  // 环境步骤仅记录「使用默认环境」的跳过决定；描述/上传走环境创建链路，不落引导字段。
  environment_skipped?: string
}

export interface PersonaPayload {
  name: string
  personality?: string
  speaking_style: string
  age?: string
  occupation?: string
  background?: string
  relationship?: string
  biological_type: string
  gender?: string
}

export const MAX_IMAGE_DESCRIPTION = 500
export const MAX_USER_TEXT = 2000

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

export function assembleCharacterPersona(answers: OnboardingAnswers): PersonaPayload {
  const payload: PersonaPayload = {
    name: answers.name?.trim() ?? '',
    biological_type: answers.biological_type?.trim() ?? '',
    speaking_style: answers.speaking_style?.trim() ?? ''
  }

  const optional = ['personality', 'gender', 'relationship', 'age', 'occupation', 'background'] as const

  for (const key of optional) {
    const value = truncate(answers[key], key === 'background' ? 2000 : 500)

    if (value) {
      payload[key] = value
    }
  }

  return payload
}
