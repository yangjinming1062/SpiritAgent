import { useCallback, useEffect, useRef, useState } from 'react'

import { $chatSessionId, getConversationRuntime } from '@/modules/conversation'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { useAutoSave } from '@/shared/hooks/use-auto-save'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { resolveReasoningEffort } from '@/shared/lib/reasoning-effort'
import { requestResource } from '@/shared/lib/resource-request'
import { AsyncSection, SaveStatus, SettingsSectionIntro } from '@/shared/panel'
import { getSpiritAgentConfig, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { SpiritAgentConfigResponse } from '@protocol'

import { InferenceForm, type InferenceFormState } from './inference-form'

const EMPTY: InferenceFormState = {
  reasoning_effort: 'low',
  enable_background_review: true,
  enable_context_compression: true,
  context_compression_threshold: 0.7,
  chat_temperature: 0.7,
  title_generation_temperature: 0.3,
  compression_temperature: 0.0
}

const readInferenceState = (config: SpiritAgentConfigResponse): InferenceFormState => ({
  reasoning_effort: resolveReasoningEffort(config.agent?.reasoning_effort),
  enable_background_review: config.agent?.enable_background_review ?? EMPTY.enable_background_review,
  enable_context_compression: config.chat?.enable_context_compression ?? EMPTY.enable_context_compression,
  context_compression_threshold: config.chat?.context_compression_threshold ?? EMPTY.context_compression_threshold,
  chat_temperature: config.agent?.temperature ?? EMPTY.chat_temperature,
  title_generation_temperature: config.chat?.title_generation_temperature ?? EMPTY.title_generation_temperature,
  compression_temperature: config.chat?.compression_temperature ?? EMPTY.compression_temperature
})

const toConfig = (state: InferenceFormState): SpiritAgentConfigResponse => ({
  agent: {
    enable_background_review: state.enable_background_review,
    reasoning_effort: state.reasoning_effort,
    temperature: state.chat_temperature
  },
  chat: {
    enable_context_compression: state.enable_context_compression,
    context_compression_threshold: state.context_compression_threshold,
    title_generation_temperature: state.title_generation_temperature,
    compression_temperature: state.compression_temperature
  }
})

const sameForm = (left: InferenceFormState, right: InferenceFormState): boolean =>
  left.reasoning_effort === right.reasoning_effort &&
  left.enable_background_review === right.enable_background_review &&
  left.enable_context_compression === right.enable_context_compression &&
  left.context_compression_threshold === right.context_compression_threshold &&
  left.chat_temperature === right.chat_temperature &&
  left.title_generation_temperature === right.title_generation_temperature &&
  left.compression_temperature === right.compression_temperature

export function InferencePage(): React.JSX.Element {
  const t = useStrings()
  const a = t.settings.inference

  const loader = useAsyncLoader<SpiritAgentConfigResponse>(() => getSpiritAgentConfig())
  const [original, setOriginal] = useState(EMPTY)
  const [state, setState] = useState(EMPTY)

  const reset = useCallback((config: SpiritAgentConfigResponse): void => {
    const next = readInferenceState(config)

    setOriginal(next)
    setState(next)
  }, [])

  const mountedRef = useRef(true)
  const latestStateRef = useRef(state)
  latestStateRef.current = state

  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (loader.data) {
      reset(loader.data)
    }
  }, [loader.data, reset])

  const persist = async (state: InferenceFormState): Promise<void> => {
    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return
    }

    const sessionId = $chatSessionId.get()
    const runtime = getConversationRuntime(sessionId)
    const visibleSettings = runtime.$sessionSettings.get()
    const { config } = await saveSpiritAgentConfig(toConfig(state))

    if (!isCurrent()) {
      return
    }

    if (mountedRef.current && sameForm(latestStateRef.current, state)) {
      reset(config)
    }

    if (sessionId) {
      try {
        const result = await requestResource('sessions.get_session', { path: { session_id: sessionId } })

        if (
          isCurrent() &&
          runtime.isCurrent() &&
          $chatSessionId.get() === sessionId &&
          runtime.$sessionSettings.get() === visibleSettings
        ) {
          runtime.hydrateSessionInfo(result.info)
        }
      } catch (error) {
        if (isCurrent()) {
          notifyError(error, t.chat.params.saveFailed)
        }
      }
    }
  }

  const { status: saveStatus, retry: retrySave } = useAutoSave({
    dirty: !sameForm(state, original),
    onError: error => notifyError(error, a.saveFailed),
    onSave: persist,
    value: state
  })

  return (
    <AsyncSection
      error={loader.error}
      errorTitle={a.loadFailed}
      isLoading={loader.isLoading}
      loadingLabel={a.loading}
      onRetry={loader.reload}
      retryLabel={t.common.retry}
    >
      <div className="space-y-6">
        <SettingsSectionIntro hint={a.intro} title={a.heading} />

        <InferenceForm state={state} update={patch => setState(current => ({ ...current, ...patch }))} />

        <SaveStatus
          errorLabel={a.saveFailed}
          onRetry={retrySave}
          retryLabel={t.common.retry}
          savedLabel={a.saved}
          savingLabel={t.common.saving}
          status={saveStatus}
        />
      </div>
    </AsyncSection>
  )
}
