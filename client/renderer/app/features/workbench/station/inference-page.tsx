import { useEffect, useRef } from 'react'

import { $chatSessionId, $sessionSettings, hydrateSessionSettings } from '@/modules/conversation'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { useAutoSave } from '@/shared/hooks/use-auto-save'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { resolveReasoningEffort } from '@/shared/lib/reasoning-effort'
import { BTN_SUBTLE, EmptyState, HINT_TEXT, LoadingBlock, SettingsSectionIntro } from '@/shared/panel'
import { getSpiritAgentConfig, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { $gateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { SessionRuntimeInfo, SpiritAgentConfigResponse } from '@protocol'

import { AgentDefaultsSection, type AgentFormState } from './inference/agent-defaults-section'
import { type ChatFormState, ContextCompressionSection } from './inference/context-compression-section'
import { type TemperatureFormState, TemperatureSection } from './inference/temperature-section'
import { useFormSection } from './use-form-section'

type InferenceForm = AgentFormState & ChatFormState & TemperatureFormState

const EMPTY: InferenceForm = {
  reasoning_effort: 'low',
  enable_background_review: true,
  enable_context_compression: true,
  context_compression_threshold: 0.7,
  chat_temperature: 0.7,
  title_generation_temperature: 0.3,
  compression_temperature: 0.0
}

const readInferenceState = (config: SpiritAgentConfigResponse): InferenceForm => ({
  reasoning_effort: resolveReasoningEffort(config.agent?.reasoning_effort),
  enable_background_review: config.agent?.enable_background_review ?? EMPTY.enable_background_review,
  enable_context_compression: config.chat?.enable_context_compression ?? EMPTY.enable_context_compression,
  context_compression_threshold: config.chat?.context_compression_threshold ?? EMPTY.context_compression_threshold,
  chat_temperature: config.agent?.temperature ?? EMPTY.chat_temperature,
  title_generation_temperature: config.chat?.title_generation_temperature ?? EMPTY.title_generation_temperature,
  compression_temperature: config.chat?.compression_temperature ?? EMPTY.compression_temperature
})

const toConfig = (state: InferenceForm): SpiritAgentConfigResponse => ({
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

const sameForm = (left: InferenceForm, right: InferenceForm): boolean =>
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
  const form = useFormSection(EMPTY, readInferenceState)
  const { reset } = form
  const mountedRef = useRef(true)
  const latestStateRef = useRef(form.state)
  latestStateRef.current = form.state

  useEffect(
    () => () => {
      mountedRef.current = false
    },
    []
  )

  // 把加载结果灌进表单 —— loader.data 一旦变化即同步。
  useEffect(() => {
    if (loader.data) {
      reset(loader.data)
    }
  }, [loader.data, reset])

  const persist = async (state: InferenceForm): Promise<void> => {
    const { config } = await saveSpiritAgentConfig(toConfig(state))

    if (mountedRef.current && sameForm(latestStateRef.current, state)) {
      reset(config)
    }

    const gateway = $gateway.get()
    const sessionId = $chatSessionId.get()
    const visibleSettings = $sessionSettings.get()

    if (gateway?.connectionState === 'open' && sessionId) {
      void gateway
        .request<{ info: SessionRuntimeInfo }>('session.set_settings', { session_id: sessionId, settings: {} })
        .then(result => {
          if ($chatSessionId.get() === sessionId && $sessionSettings.get() === visibleSettings) {
            hydrateSessionSettings(result.info)
          }
        })
        .catch(error => notifyError(error, t.chat.params.saveFailed))
    }
  }

  const { status: saveStatus } = useAutoSave({
    dirty: form.isDirty,
    onError: error => notifyError(error, a.saveFailed),
    onSave: persist,
    value: form.state
  })

  if (loader.isLoading) {
    return <LoadingBlock label={a.loading} />
  }

  if (loader.error) {
    return (
      <EmptyState
        action={
          <button className={BTN_SUBTLE} onClick={loader.reload} type="button">
            {t.common.retry}
          </button>
        }
        description={backendDetailMessage(loader.error, a.loadFailed)}
        title={a.heading}
      />
    )
  }

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={a.intro} title={a.heading} />

      <AgentDefaultsSection disabled={false} state={form.state} t={a.agentDefaults} update={form.set} />

      <ContextCompressionSection disabled={false} state={form.state} t={a.contextCompression} update={form.set} />

      <TemperatureSection disabled={false} state={form.state} t={a.temperature} update={form.set} />

      <div className="flex min-h-5 justify-end pt-2">
        {saveStatus === 'saving' && <span className={HINT_TEXT}>{t.common.saving}</span>}
        {saveStatus === 'saved' && <span className={HINT_TEXT}>{a.saved}</span>}
        {saveStatus === 'error' && <span className="text-[10px] leading-relaxed text-danger-fg">{a.saveFailed}</span>}
      </div>
    </div>
  )
}
