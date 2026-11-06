import type { Phase, VoiceStage } from './back-transition'

/** 引导阶段状态：四个维度一起迁移，避免分散 setState 产生不可能的中间组合。 */
export interface OnboardingStageState {
  phase: Phase
  qIndex: number
  voiceStage: VoiceStage
  imageSealed: boolean
}

export interface StageIntent {
  phase: Phase
  qIndex?: number
  voiceStage?: VoiceStage
  imageSealed?: boolean
}

export const initialOnboardingStage: OnboardingStageState = {
  imageSealed: false,
  phase: 'q-character',
  qIndex: 0,
  voiceStage: 'describe'
}

/** 进入声音选择：锁定形象输入，回到目录态。 */
export function enterVoiceSelection(stage: OnboardingStageState): OnboardingStageState {
  return { ...stage, imageSealed: true, phase: 'voice', qIndex: 0, voiceStage: 'catalog' }
}

/** 下一步：声音描述阶段先切目录，否则推进问题，角色问答结束后进入背景页。 */
export function advanceStage(stage: OnboardingStageState, questionCount: number): OnboardingStageState {
  if (stage.phase === 'voice' && stage.voiceStage === 'describe') {
    return { ...stage, voiceStage: 'catalog' }
  }

  if (stage.qIndex < questionCount - 1) {
    return { ...stage, qIndex: stage.qIndex + 1 }
  }

  if (stage.phase === 'q-character') {
    return { ...stage, phase: 'background' }
  }

  return stage
}

/** 回退：只采纳意图中出现的字段，未出现的保持当前值。 */
export function backStage(stage: OnboardingStageState, intent: StageIntent): OnboardingStageState {
  return {
    ...stage,
    phase: intent.phase,
    ...(intent.qIndex !== undefined ? { qIndex: intent.qIndex } : {}),
    ...(intent.voiceStage !== undefined ? { voiceStage: intent.voiceStage } : {})
  }
}

/** 恢复草稿：恢复目标可能同时带回问题下标、声音阶段与形象锁定。 */
export function resumeStage(stage: OnboardingStageState, phase: Phase, target: StageIntent): OnboardingStageState {
  return {
    ...stage,
    phase,
    ...(target.qIndex !== undefined ? { qIndex: target.qIndex } : {}),
    ...(target.imageSealed ? { imageSealed: true } : {}),
    ...(target.voiceStage ? { voiceStage: target.voiceStage } : {})
  }
}
