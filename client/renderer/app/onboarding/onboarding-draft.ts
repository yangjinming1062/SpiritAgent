import { assembleCharacterPersona, assemblePersona, type OnboardingAnswers } from '@/features/character'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage, ipcErrorStatus, isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { authedResource, requestResource } from '@/shared/lib/resource-request'
import { sleep } from '@runtime'

import type { ApiSchemas } from '../../../../shared/api.generated'

import { type QKey, USER_QUESTIONS } from './onboarding-questions'

type OnboardingResumeState = ApiSchemas['OnboardingStateResponse']
type PersonaRequest = ApiSchemas['PersonaUpdate']
export const ONBOARDING_PERSONA_FIELDS = [
  'name',
  'biological_type',
  'gender',
  'relationship',
  'personality',
  'speaking_style',
  'age',
  'occupation',
  'background'
] as const satisfies readonly (keyof NonNullable<PersonaRequest['expected_fields']>)[]
export type OnboardingPersonaConflict = Partial<Record<(typeof ONBOARDING_PERSONA_FIELDS)[number], string | null>>

// 串行草稿写入归引导视图持有；释放后，排队任务与重试均不能使用新账户。
export class OnboardingDraft {
  private active = false
  private generation = 0
  private submissions: Promise<void> = Promise.resolve()
  private personaBase: Record<string, string> | null = null
  private conflictingPersona: Record<string, string> | null = null

  personaConflict(): OnboardingPersonaConflict | null {
    if (!this.conflictingPersona || !this.personaBase) {
      return null
    }

    const changed: OnboardingPersonaConflict = {}

    for (const key of ONBOARDING_PERSONA_FIELDS) {
      if ((this.conflictingPersona[key] || null) !== (this.personaBase[key] || null)) {
        changed[key] = this.conflictingPersona[key] || null
      }
    }

    return changed
  }

  reviewPersonaConflict(): void {
    if (this.conflictingPersona) {
      this.personaBase = this.conflictingPersona
      this.conflictingPersona = null
    }
  }

  start(): void {
    this.active = true
    this.generation += 1
    this.personaBase = null
    this.conflictingPersona = null
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

      if (this.personaBase && ONBOARDING_PERSONA_FIELDS.some(key => key === field)) {
        return true
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

      if (!isCurrent()) {
        return null
      }

      this.personaBase = state.persona_complete ? { ...state.answers } : null
      this.conflictingPersona = null

      return state
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

      for (const { key } of USER_QUESTIONS) {
        if (
          completeAnswers[key] !== undefined &&
          !(await this.retry(() => this.submit(key, completeAnswers[key] || null), isCurrent))
        ) {
          return '资料保存失败，请检查网络后再点「完成」重试'
        }
      }

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

    const body: PersonaRequest = { definition_json: { ...payload } }

    if (this.personaBase) {
      body.definition_json = {}
      body.expected_fields = {}

      for (const key of ONBOARDING_PERSONA_FIELDS) {
        const value = payload[key] || null
        const original = this.personaBase[key] || null

        if (value !== original) {
          body.definition_json[key] = value
          body.expected_fields[key] = original
        }
      }

      if (Object.keys(body.definition_json).length === 0) {
        return true
      }
    }

    const result = await authedResource('companion.put_persona', { body })

    if (!isCurrent() || (!result.ok && result.reason === 'unauth')) {
      return false
    }

    if (result.ok && result.value) {
      this.recordPersonaSave(body, result.value)
      this.conflictingPersona = null

      return true
    }

    const latest = await authedResource('companion.get_persona')

    if (!isCurrent()) {
      return false
    }

    const fresh = latest.ok ? latest.value : null

    if (fresh) {
      if (
        fresh.is_complete &&
        Object.entries(body.definition_json).every(
          ([key, value]) => (fresh.definition_json[key] || null) === (value || null)
        )
      ) {
        this.recordPersonaSave(body, fresh)
        this.conflictingPersona = null

        return true
      }

      if (!result.ok && ipcErrorStatus(result.error) === 409) {
        this.conflictingPersona = fresh.definition_json
        // 另一端已完成首次保存时，本地仍以空起点核对，显式确认后才采用最新起点。
        this.personaBase ??= {}
      }
    }

    if (result.ok) {
      return false
    }

    if (isClientErrorIpc(result.error)) {
      throw result.error
    }

    log.warn('onboarding', 'persona save failed', result.error)

    return false
  }

  private recordPersonaSave(body: PersonaRequest, response: ApiSchemas['PersonaResponse']): void {
    const base = { ...this.personaBase }

    for (const key of ONBOARDING_PERSONA_FIELDS) {
      if (key in body.definition_json) {
        base[key] = response.definition_json[key] || ''
      }
    }

    this.personaBase = base
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
