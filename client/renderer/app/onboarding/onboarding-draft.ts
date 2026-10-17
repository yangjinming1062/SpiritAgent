import { assembleCharacterPersona, assemblePersona, type OnboardingAnswers } from '@/modules/character'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage, isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { authedResource, requestResource } from '@/shared/lib/resource-request'
import { sleep } from '@runtime'

import type { ApiSchemas } from '../../../../shared/api.generated'

import type { QKey } from './onboarding-questions'

export type OnboardingResumeState = ApiSchemas['OnboardingStateResponse']

// 串行草稿写入归引导视图持有；释放后，排队任务与重试均不能使用新账户。
export class OnboardingDraft {
  private active = false
  private generation = 0
  private submissions: Promise<void> = Promise.resolve()

  start(): void {
    this.active = true
    this.generation += 1
  }

  dispose(): void {
    this.active = false
    this.generation += 1
  }

  captureOperation(): () => boolean {
    const generation = this.generation
    const scope = captureAuthScope()

    return () => this.active && this.generation === generation && Boolean(scope?.())
  }

  submit(field: QKey, value: string | null): Promise<boolean> {
    // 在入队时捕获账户，不能等到前一个请求结束才捕获。
    const isCurrent = this.captureOperation()

    const submission = this.submissions.then(async () => {
      if (!isCurrent()) {
        return false
      }

      try {
        await requestResource('onboarding.patch_onboarding', { body: { field, value } })

        return isCurrent()
      } catch (error) {
        if (isCurrent()) {
          log.warn('onboarding', `onboarding ${field} failed`, error)
        }

        return false
      }
    })

    this.submissions = submission.then(() => undefined)

    return submission
  }

  async readResumeState(isCurrent: () => boolean): Promise<OnboardingResumeState | null> {
    if (!isCurrent()) {
      return null
    }

    try {
      const state = await requestResource('onboarding.get_onboarding')

      return isCurrent() ? state : null
    } catch (error) {
      if (isCurrent()) {
        log.warn('onboarding', 'resume state failed', error)
      }

      return null
    }
  }

  async saveCharacter(answers: OnboardingAnswers): Promise<string | null> {
    const isCurrent = this.captureOperation()
    await this.submissions

    try {
      const saved = await this.retry(() => this.savePersona(assembleCharacterPersona(answers), isCurrent), isCurrent)

      return saved ? null : '角色资料保存失败，请检查网络后重试'
    } catch (error) {
      if (isCurrent()) {
        log.warn('onboarding', 'character persona save rejected', error)
      }

      return backendDetailMessage(error, '角色资料保存失败，请检查填写内容后重试')
    }
  }

  async saveComplete(answers: OnboardingAnswers, selectedVoice: string | null): Promise<string | null> {
    const isCurrent = this.captureOperation()
    const completeAnswers = { ...answers }

    if (selectedVoice && !completeAnswers.voice) {
      completeAnswers.voice = selectedVoice
    }

    try {
      const voiceSaved = !selectedVoice || (await this.retry(() => this.submit('voice', selectedVoice), isCurrent))
      await this.submissions

      const saved =
        voiceSaved && (await this.retry(() => this.savePersona(assemblePersona(completeAnswers), isCurrent), isCurrent))

      return saved ? null : '资料保存失败，请检查网络后再点「完成」重试'
    } catch (error) {
      if (isCurrent()) {
        log.warn('onboarding', 'final persona save rejected', error)
      }

      return backendDetailMessage(error, '资料保存失败，请检查填写内容后重试')
    }
  }

  private async savePersona(payload: ReturnType<typeof assemblePersona>, isCurrent: () => boolean): Promise<boolean> {
    if (!isCurrent()) {
      return false
    }

    const result = await authedResource('companion.put_persona', {
      body: { definition_json: JSON.stringify(payload) }
    })

    if (!isCurrent() || (!result.ok && result.reason === 'unauth')) {
      return false
    }

    if (result.ok) {
      return true
    }

    if (isClientErrorIpc(result.error)) {
      throw result.error
    }

    log.warn('onboarding', 'persona save failed', result.error)

    return false
  }

  private async retry(fn: () => Promise<boolean>, isCurrent: () => boolean): Promise<boolean> {
    for (let attempt = 0; attempt < 3 && isCurrent(); attempt += 1) {
      if (await fn()) {
        return isCurrent()
      }

      if (attempt < 2 && isCurrent()) {
        await sleep(700)
      }
    }

    return false
  }
}
