// 引导阶段类型，以及 ``onBack`` 转移决策的纯函数（与组件状态机隔离）。输出是下一个状态意图或 null(表示"无变化")。

export type Phase =
  | 'q-character'
  | 'portrait-generate'
  | 'hatching'
  | 'portrait-avatar'
  | 'fullbody-reference'
  | 'q-user'
  | 'voice'
  | 'finishing'

export type VoiceStage = 'describe' | 'catalog'

interface BackState {
  phase: Phase
  qIndex: number
  voiceStage: VoiceStage
  imageSealed: boolean
  // 已直接采用头像时返回制作表单，保留当前头像。
  portraitDirectAdopt: boolean
}

interface BackIntent {
  phase: Phase
  qIndex?: number
  voiceStage?: VoiceStage
}

export function computeBackTransition(state: BackState, characterQuestionsCount: number): BackIntent | null {
  if (state.phase === 'q-user') {
    return { phase: 'voice', voiceStage: 'catalog' }
  }

  if (state.phase === 'voice') {
    if (state.voiceStage === 'catalog') {
      return { phase: 'voice', voiceStage: 'describe' }
    }

    return state.imageSealed ? null : { phase: 'fullbody-reference' }
  }

  if (state.imageSealed) {
    return null
  }

  if (state.phase === 'fullbody-reference') {
    return state.portraitDirectAdopt ? { phase: 'portrait-generate' } : { phase: 'portrait-avatar' }
  }

  if (state.phase === 'portrait-avatar') {
    return { phase: 'portrait-generate' }
  }

  if (state.phase === 'portrait-generate') {
    return { phase: 'q-character', qIndex: characterQuestionsCount - 1 }
  }

  if (state.phase === 'q-character') {
    return state.qIndex > 0 ? { phase: 'q-character', qIndex: state.qIndex - 1 } : null
  }

  return null
}
