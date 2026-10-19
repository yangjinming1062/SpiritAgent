import { atom } from 'nanostores'

import { cloneVoice, designVoice, sampleLine, type VoiceDesignPreview } from '@/modules/speech'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { getStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

import type { VoiceSample } from './use-voice-sample'

export type VoiceTab = 'fixed' | 'design' | 'clone'
export type VoiceOperation = 'idle' | 'describing' | 'generating' | 'confirming' | 'cancelling'

interface VoiceConfirmation {
  id: string
  label: string
  preview: VoiceDesignPreview
}

interface VoiceCreationState {
  sessionId: string | null
  tab: VoiceTab
  prompt: string
  provider: string | undefined
  feedback: string
  adjusting: boolean
  designPreview: VoiceDesignPreview | null
  clonePreview: VoiceDesignPreview | null
  cloneSampleId: string | null
  sample: VoiceSample | null
  phase: VoiceOperation
  error: string | null
  outcome: 'saved' | 'cancelled' | 'unknown' | null
  startedAt: number | undefined
  operationId: string | null
  confirmation: VoiceConfirmation | null
  confirmationChecked: boolean
  initializedPrompt: boolean
  initializedTab: boolean
}

const emptyState = (sessionId: string | null): VoiceCreationState => ({
  sessionId,
  tab: 'fixed',
  prompt: '',
  provider: undefined,
  feedback: '',
  adjusting: false,
  designPreview: null,
  clonePreview: null,
  cloneSampleId: null,
  sample: null,
  phase: 'idle',
  error: null,
  outcome: null,
  startedAt: undefined,
  operationId: null,
  confirmation: null,
  confirmationChecked: false,
  initializedPrompt: false,
  initializedTab: false
})

export const $voiceCreation = atom<VoiceCreationState>(emptyState(null))
let operationVersion = 0

function resetVoiceCreation(sessionId: string | null): void {
  const previous = $voiceCreation.get()
  operationVersion += 1
  $voiceCreation.set(emptyState(sessionId))

  if (previous.operationId && previous.sessionId && previous.phase !== 'confirming') {
    void backendApi()
      .api({
        operation: 'voices.delete_voice_operation',
        input: { path: { operation_id: encodeURIComponent(previous.operationId) } },
        authSessionId: previous.sessionId
      })
      .catch(error => log.warn('voice-creation', 'Account cleanup could not cancel voice operation', error))
  }
}

function syncVoiceCreationSession(): void {
  const auth = $auth.get()
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null

  if ($voiceCreation.get().sessionId !== sessionId) {
    resetVoiceCreation(sessionId)
  }
}

registerStorageClearHandler(() => resetVoiceCreation(null))
$auth.listen(syncVoiceCreationSession)
syncVoiceCreationSession()

export function updateVoiceCreation(patch: Partial<VoiceCreationState>): void {
  syncVoiceCreationSession()

  if ($voiceCreation.get().sessionId) {
    $voiceCreation.set({ ...$voiceCreation.get(), ...patch })
  }
}

export function setVoiceCreationSample(sample: VoiceSample | null): void {
  const state = $voiceCreation.get()
  updateVoiceCreation({
    sample,
    clonePreview: null,
    cloneSampleId: null,
    ...(state.phase === 'idle' && state.outcome !== 'unknown' ? { outcome: null } : {})
  })
}

function beginOperation(phase: VoiceOperation, recovering = false): { id: string; live: () => boolean } | null {
  syncVoiceCreationSession()
  const state = $voiceCreation.get()
  const scope = captureAuthScope()

  if (!scope || state.phase !== 'idle' || state.operationId || (state.outcome === 'unknown' && !recovering)) {
    return null
  }

  const id = crypto.randomUUID()
  const version = ++operationVersion
  const live = (): boolean => scope() && version === operationVersion && $voiceCreation.get().operationId === id
  updateVoiceCreation({
    phase,
    operationId: id,
    error: null,
    outcome: null,
    confirmationChecked: false,
    startedAt: Date.now()
  })

  return { id, live }
}

// 制作请求归账户会话持有，页面只订阅结果；录音、样本上传、播放与确认回调仍受活动资格约束。
async function createVoice(
  phase: 'describing' | 'generating',
  action: (id: string, live: () => boolean) => Promise<VoiceDesignPreview | null>,
  sampleId?: string
): Promise<VoiceDesignPreview | null> {
  const operation = beginOperation(phase)

  if (!operation) {
    return null
  }

  updateVoiceCreation({ confirmation: null, initializedPrompt: true, initializedTab: true })

  try {
    const preview = await action(operation.id, operation.live)

    if (!operation.live() || !preview) {
      return null
    }

    if (preview.kind === 'clone' && $voiceCreation.get().sample?.sample_id !== sampleId) {
      return null
    }

    updateVoiceCreation({
      [preview.kind === 'design' ? 'designPreview' : 'clonePreview']: preview,
      ...(preview.kind === 'clone' ? { cloneSampleId: sampleId ?? null } : {}),
      feedback: '',
      adjusting: false
    })

    return preview
  } catch (error) {
    log.warn('voice-creation', 'Voice creation failed', error)

    if (operation.live()) {
      updateVoiceCreation({
        outcome: isClientErrorIpc(error) ? null : 'unknown',
        error: errorMessage(error, getStrings().voice.designFailed)
      })
    }

    return null
  } finally {
    if (operation.live()) {
      updateVoiceCreation({
        phase: 'idle',
        ...($voiceCreation.get().outcome === 'unknown' ? {} : { operationId: null })
      })
    }
  }
}

export function createDesignedVoice(
  mode: 'auto' | 'feedback' | 'preview',
  name: string,
  preference?: string
): Promise<VoiceDesignPreview | null> {
  const state = $voiceCreation.get()
  const previewText = sampleLine(name)

  return createVoice(mode === 'preview' ? 'generating' : 'describing', async (id, live) => {
    let description = state.prompt.trim()
    let provider = state.provider

    if (mode !== 'preview') {
      const generated = await requestResource('voices.post_voice_prompt', {
        body: {
          operation_id: id,
          preference: preference?.trim() || undefined,
          current_prompt: mode === 'feedback' ? description : undefined,
          feedback: mode === 'feedback' ? state.feedback.trim() : undefined
        }
      })

      if (!live()) {
        return null
      }

      description = generated.prompt
      provider = generated.provider
      updateVoiceCreation({ prompt: description, provider, designPreview: null })
    }

    if (!live()) {
      return null
    }

    updateVoiceCreation({ phase: 'generating' })

    return designVoice(description, previewText, id, provider)
  })
}

export function createClonedVoice(name: string): Promise<VoiceDesignPreview | null> {
  const sample = $voiceCreation.get().sample

  if (!sample) {
    return Promise.resolve(null)
  }

  return createVoice('generating', id => cloneVoice(sample.sample_id, sampleLine(name), id), sample.sample_id)
}

export async function cancelVoiceCreation(): Promise<void> {
  const state = $voiceCreation.get()
  const scope = captureAuthScope()

  if (!scope || !state.operationId || state.phase === 'confirming' || state.phase === 'cancelling') {
    return
  }

  const version = ++operationVersion
  const live = (): boolean => scope() && version === operationVersion
  updateVoiceCreation({ phase: 'cancelling', error: null })

  try {
    const result = await requestResource('voices.delete_voice_operation', {
      path: { operation_id: encodeURIComponent(state.operationId) }
    })

    if (!live()) {
      return
    }

    if (!result.cancelled) {
      throw new Error(getStrings().voice.cancelFailed)
    }

    updateVoiceCreation({ phase: 'idle', operationId: null, outcome: 'cancelled', error: null })
  } catch (error) {
    log.warn('voice-creation', 'Could not cancel voice operation', error)

    if (live()) {
      updateVoiceCreation({ phase: 'idle', outcome: 'unknown', error: getStrings().voice.cancelFailed })
    }
  }
}

export function beginVoiceConfirmation(recovering = false): {
  live: () => boolean
  finish: () => void
  invalidate: () => void
} | null {
  const operation = beginOperation('confirming', recovering)

  if (!operation) {
    return null
  }

  return {
    live: operation.live,
    finish: () => {
      if (operation.live()) {
        updateVoiceCreation({ phase: 'idle', operationId: null })
      }
    },
    invalidate: () => {
      if (operation.live()) {
        operationVersion += 1
        updateVoiceCreation({
          phase: 'idle',
          operationId: null,
          ...($voiceCreation.get().confirmation ? { outcome: 'unknown', error: getStrings().voice.confirmUnknown } : {})
        })
      }
    }
  }
}
