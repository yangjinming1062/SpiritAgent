import { useEffect, useState } from 'react'

import { $chatSessionId, $sessionSettings, hydrateSessionSettings } from '@/modules/conversation'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { triggerHaptic } from '@/shared/lib/haptics'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { resolveReasoningEffort } from '@/shared/lib/reasoning-effort'
import { BTN_PRIMARY, BTN_SUBTLE, EmptyState, LoadingBlock, SettingsSectionIntro, Spinner } from '@/shared/panel'
import { getSpiritAgentConfig, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { $gateway } from '@/shared/store/gateway'
import { notify, notifyError } from '@/shared/store/notifications'
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

export function InferencePage(): React.JSX.Element {
  const t = useStrings()
  const a = t.settings.inference

  const loader = useAsyncLoader<SpiritAgentConfigResponse>(() => getSpiritAgentConfig())
  const [isSaving, setIsSaving] = useState(false)

  const form = useFormSection(EMPTY, readInferenceState)
  const { isDirty, reset } = form

  // 把加载结果灌进表单 —— loader.data 一旦变化即同步。
  useEffect(() => {
    if (loader.data) {
      reset(loader.data)
    }
  }, [loader.data, reset])

  const handleSave = async () => {
    try {
      setIsSaving(true)

      const { config } = await saveSpiritAgentConfig({
        agent: {
          enable_background_review: form.state.enable_background_review,
          reasoning_effort: form.state.reasoning_effort,
          temperature: form.state.chat_temperature
        },
        chat: {
          enable_context_compression: form.state.enable_context_compression,
          context_compression_threshold: form.state.context_compression_threshold,
          title_generation_temperature: form.state.title_generation_temperature,
          compression_temperature: form.state.compression_temperature
        }
      })

      reset(config)
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

      triggerHaptic('success')
      notify({ kind: 'success', title: a.heading, message: a.saved })
    } catch (err) {
      notifyError(err, a.saveFailed)
    } finally {
      setIsSaving(false)
    }
  }

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

      <AgentDefaultsSection disabled={isSaving} state={form.state} t={a.agentDefaults} update={form.set} />

      <ContextCompressionSection disabled={isSaving} state={form.state} t={a.contextCompression} update={form.set} />

      <TemperatureSection disabled={isSaving} state={form.state} t={a.temperature} update={form.set} />

      <div className="flex justify-end pt-2">
        <button className={BTN_PRIMARY} disabled={isSaving || !isDirty} onClick={() => void handleSave()} type="button">
          {isSaving && <Spinner className="size-3.5" />}
          {isSaving ? t.common.saving : t.common.save}
        </button>
      </div>
    </div>
  )
}
