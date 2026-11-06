import { useEffect, useRef } from 'react'

import { applyServerCompanionVoiceId, setCompanionVoiceId } from '@/features/character'
import { fetchVoiceCatalogRaw, type VoiceCatalog, type VoiceDesignPreview } from '@/features/speech'
import { failureText, isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import type { getStrings } from '@/shared/strings'

import { $voiceCreation, beginVoiceConfirmation, cancelVoiceCreation, updateVoiceCreation } from '../voice-creation-store'

type VoiceStrings = ReturnType<typeof getStrings>['voice']

export type VoiceSelectionHandler = (
  voiceId: string,
  label: string,
  serverConfirmed: boolean,
  isSelectionCurrent?: () => boolean
) => Promise<boolean | void>

interface VoiceOperationDeps {
  active: boolean
  begin: () => () => boolean
  clearPlaybackError: () => void
  clearSampleError: () => void
  disabled: boolean
  locale: string
  onCatalog: (catalog: VoiceCatalog) => void
  onSaved: () => void
  onVoiceSelected?: VoiceSelectionHandler
  sampleBusy: boolean
  stopPreview: () => void
  t: VoiceStrings
  viewEligible: () => boolean
}

/** 声音制作结果的确认与保存：请求与任务生命周期归这里，页面只消费动作。 */
export function useVoiceOperations({
  active,
  begin,
  clearPlaybackError,
  clearSampleError,
  disabled,
  locale,
  onCatalog,
  onSaved,
  onVoiceSelected,
  sampleBusy,
  stopPreview,
  t,
  viewEligible
}: VoiceOperationDeps) {
  const operation = useRef<ReturnType<typeof beginVoiceConfirmation>>(null)

  // 离开活动资格时不保留确认身份：旧页面不能继续完成或取消别的会话的确认。
  useEffect(() => {
    if (!active) {
      operation.current?.invalidate()
      operation.current = null
    }

    return () => {
      operation.current?.invalidate()
      operation.current = null
    }
  }, [active])

  const runOperation = async (
    action: (live: () => boolean) => Promise<void>,
    recovering = false
  ): Promise<void> => {
    if (disabled || sampleBusy || !viewEligible() || operation.current) {
      return
    }

    const claim = beginVoiceConfirmation(recovering)

    if (!claim) {
      return
    }

    operation.current = claim
    const mounted = begin()
    const live = (): boolean => mounted() && viewEligible() && operation.current === claim && claim.live()
    stopPreview()
    clearPlaybackError()
    clearSampleError()

    try {
      await action(live)
    } catch (failure) {
      log.warn('voice-manager', 'Voice confirmation failed', failure)

      if (live()) {
        const definiteRejection = isClientErrorIpc(failure)
        const unknown = !definiteRejection && $voiceCreation.get().confirmation !== null
        const rejected = definiteRejection ? $voiceCreation.get().confirmation?.preview : null

        updateVoiceCreation({
          outcome: unknown ? 'unknown' : null,
          error: failureText(failure, t.saveFailed),
          ...(rejected
            ? {
                confirmation: null,
                confirmationChecked: false,
                ...(rejected.kind === 'design' ? { designPreview: null } : { clonePreview: null, cloneSampleId: null })
              }
            : {})
        })
      }
    } finally {
      claim.finish()

      if (operation.current === claim) {
        operation.current = null
      }
    }
  }

  const applyVoice = async (
    id: string,
    label: string,
    serverConfirmed: boolean,
    live: () => boolean
  ): Promise<void> => {
    if (onVoiceSelected) {
      if ((await onVoiceSelected(id, label, serverConfirmed, live)) === false) {
        throw new Error(t.saveFailed)
      }
    } else if (serverConfirmed) {
      await applyServerCompanionVoiceId(id)
    } else {
      setCompanionVoiceId(id)
    }
  }

  const adoptVoice = (id: string, label: string, preview?: VoiceDesignPreview, recovering = false): void => {
    if (preview) {
      const latest = $voiceCreation.get()

      const candidate = recovering
        ? latest.confirmation?.preview
        : preview.kind === 'design'
          ? latest.designPreview
          : latest.clonePreview

      const matchingInput =
        recovering ||
        (preview.kind === 'design'
          ? latest.prompt.trim() === preview.prompt
          : Boolean(latest.cloneSampleId && latest.cloneSampleId === latest.sample?.sample_id))

      if (
        !matchingInput ||
        candidate?.candidateId !== preview.candidateId ||
        candidate.expectedVersion !== preview.expectedVersion ||
        candidate.voiceId !== id
      ) {
        return
      }
    }

    void runOperation(async live => {
      let voiceId = id

      if (preview) {
        updateVoiceCreation({ confirmation: { id, label, preview } })

        const result = await requestResource('voices.post_voice_confirmation', {
          path: { candidate_id: preview.candidateId },
          body: {
            expected_version: preview.expectedVersion
          }
        })

        if (!live()) {
          return
        }

        voiceId = result.voice_id
      }

      await applyVoice(voiceId, label, Boolean(preview), live)

      if (live()) {
        updateVoiceCreation({
          confirmation: null,
          outcome: 'saved',
          ...(preview?.kind === 'design'
            ? { designPreview: null }
            : preview?.kind === 'clone'
              ? { clonePreview: null }
              : {})
        })

        onSaved()
      }
    }, recovering)
  }

  const checkConfirmation = async (): Promise<void> => {
    const pending = $voiceCreation.get().confirmation

    if (!pending) {
      return
    }

    await runOperation(async live => {
      updateVoiceCreation({ confirmationChecked: false })
      const result = await fetchVoiceCatalogRaw(locale)

      if (!live()) {
        return
      }

      if (!result.ok) {
        throw new Error(t.loadFailed)
      }

      onCatalog(result.catalog)

      if (result.catalog.customVoices.some(voice => voice.voice_id === pending.id)) {
        await requestResource('voices.post_voice_confirmation', {
          path: { candidate_id: pending.preview.candidateId },
          body: { expected_version: pending.preview.expectedVersion }
        })

        if (!live()) {
          return
        }

        await applyVoice(pending.id, pending.label, true, live)

        if (live()) {
          updateVoiceCreation({ confirmation: null, designPreview: null, clonePreview: null, outcome: 'saved' })
        }
      } else {
        updateVoiceCreation({ confirmationChecked: true, outcome: 'unknown' })
      }
    }, true)
  }

  const stopOperation = async (): Promise<void> => {
    stopPreview()
    await cancelVoiceCreation()
  }

  return { adoptVoice, checkConfirmation, stopOperation }
}
