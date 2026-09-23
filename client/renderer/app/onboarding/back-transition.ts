// ``onBack`` 转移决策的纯函数,便于测试且隔离状态机逻辑。
// 输出是下一个状态意图或 null(表示"无变化")。

type BackPhase =
  | 'q-character'
  | 'q-user'
  | 'voice'
  | 'fullbody-reference'
  | 'portrait-choose'
  | 'portrait-avatar'
  | 'hatching'
  | 'finishing'
  | 'greeting'
type BackVoiceStage = 'describe' | 'catalog'

interface BackState {
  phase: BackPhase
  qIndex: number
  voiceStage: BackVoiceStage
  imageSealed: boolean
  // 头像未经确认步骤直接采用（自备图或「继续当前头像」）时为 true——全身阶段返回选择步骤而非确认步骤。
  portraitDirectAdopt: boolean
}

interface BackIntent {
  phase: BackPhase
  qIndex?: number
  voiceStage?: BackVoiceStage
}

export function computeBackTransition(state: BackState, characterQuestionsCount: number): BackIntent | null {
  if (state.phase === 'q-user') {
    return state.qIndex > 0 ? { phase: 'q-user', qIndex: state.qIndex - 1 } : { phase: 'voice', voiceStage: 'catalog' }
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
    return state.portraitDirectAdopt ? { phase: 'portrait-choose' } : { phase: 'portrait-avatar' }
  }

  if (state.phase === 'portrait-avatar') {
    return { phase: 'portrait-choose' }
  }

  if (state.phase === 'portrait-choose') {
    return { phase: 'q-character', qIndex: characterQuestionsCount - 1 }
  }

  if (state.phase === 'q-character') {
    return state.qIndex > 0 ? { phase: 'q-character', qIndex: state.qIndex - 1 } : null
  }

  return null
}
