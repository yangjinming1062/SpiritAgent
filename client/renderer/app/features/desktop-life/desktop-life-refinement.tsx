import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import {
  $desktopVideoSets,
  type DesktopVideoAction,
  type DesktopVideoRefinementPreview,
  type DesktopVideoRefinementSource,
  type DesktopVideoSet,
  previewDesktopVideoRefinement,
  refineDesktopVideo,
  refreshDesktopVideos
} from '@/modules/desktop-videos'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { CreationActionBar, CreationFeedback, INPUT_CLASS, WizardModal } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { errorMessage } from '@runtime'

import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE } from './desktop-life-buttons'
import { DesktopLifePreview } from './desktop-life-preview'
import { useDesktopLifeStrings } from './desktop-life-strings'

export function VideoRefinementModal({
  action,
  set,
  source,
  onClose
}: {
  action: DesktopVideoAction
  set: DesktopVideoSet
  source: DesktopVideoRefinementSource
  onClose: () => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const active = usePanelActivity()
  const sets = useStore($desktopVideoSets)

  const [feedback, setFeedback] = useState(
    (source === 'candidate' ? action.draft?.video_feedback : action.current?.video_feedback) ?? ''
  )

  const [fitAspect, setFitAspect] = useState(false)
  const [previewAttempt, setPreviewAttempt] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [submissionError, setSubmissionError] = useState<string | null>(null)
  const submittingRequest = useRef(false)

  const [checked, setChecked] = useState<{
    key: string
    result?: DesktopVideoRefinementPreview
    error?: string
  } | null>(null)

  const beginAsync = useAsyncGuard()
  const liveSet = sets.find(item => item.id === set.id)
  const liveAction = liveSet?.actions.find(item => item.id === action.id)
  const sourceUrl = source === 'candidate' ? action.draft?.candidate_video_url : action.current?.video_url

  const liveSourceUrl = source === 'candidate' ? liveAction?.draft?.candidate_video_url : liveAction?.current?.video_url

  const stale =
    !liveSet ||
    !liveAction ||
    liveSet.base_version !== set.base_version ||
    liveSet.resolution_revision !== set.resolution_revision ||
    liveAction.version !== action.version ||
    liveAction.draft?.id !== action.draft?.id ||
    liveAction.status === 'processing' ||
    liveSourceUrl?.split(/[?#]/)[0] !== sourceUrl?.split(/[?#]/)[0]

  const inputKey = JSON.stringify([
    set.id,
    set.base_version,
    set.resolution_revision,
    action.id,
    action.version,
    action.draft?.id,
    source,
    sourceUrl?.split(/[?#]/)[0],
    feedback,
    fitAspect,
    previewAttempt
  ])

  const preview = checked?.key === inputKey ? checked.result : undefined
  const previewError = checked?.key === inputKey ? checked.error : undefined

  useEffect(() => {
    if (!active || stale) {
      return
    }

    const scope = captureAuthScope()
    const isLive = beginAsync()
    let current = true

    const timer = window.setTimeout(() => {
      void previewDesktopVideoRefinement(action, set, source, feedback, fitAspect ? 'expand' : 'keep')
        .then(result => {
          if (current && scope?.() && isLive()) {
            setChecked({ key: inputKey, result })
          }
        })
        .catch((error: unknown) => {
          if (current && scope?.() && isLive()) {
            setChecked({ key: inputKey, error: backendDetailMessage(error, errorMessage(error)) })
          }
        })
    }, 300)

    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [action, active, beginAsync, feedback, fitAspect, inputKey, set, source, stale])

  const submit = async (): Promise<void> => {
    if (submittingRequest.current || !active || stale || !preview || (!feedback.trim() && !fitAspect)) {
      return
    }

    const scope = captureAuthScope()
    const isLive = beginAsync()
    submittingRequest.current = true
    setSubmitting(true)
    setSubmissionError(null)

    try {
      await refineDesktopVideo(action, set, source, feedback, fitAspect ? 'expand' : 'keep')

      if (scope?.() && isLive()) {
        onClose()
      }
    } catch (failure) {
      if (scope?.() && isLive()) {
        setSubmissionError(backendDetailMessage(failure, errorMessage(failure)))
        notifyError(failure, t.refineVideo)
        void refreshDesktopVideos()
      }
    } finally {
      submittingRequest.current = false

      if (scope?.() && isLive()) {
        setSubmitting(false)
      }
    }
  }

  const operationFeedback =
    submitting || stale || submissionError || previewError || !preview ? (
      <CreationFeedback
        actions={
          previewError && !stale && !submitting ? (
            <button className={BTN_SUBTLE} onClick={() => setPreviewAttempt(attempt => attempt + 1)} type="button">
              {t.retryPrompt}
            </button>
          ) : undefined
        }
        description={submitting ? t.savedCurrent : stale ? t.refinementStale : submissionError || previewError}
        key={inputKey}
        state={submitting ? 'working' : stale || submissionError || previewError ? 'error' : 'working'}
        title={
          submitting
            ? t.submittingRefinement
            : stale || submissionError || previewError
              ? t.operationFailed
              : t.refinementPreviewLoading
        }
      />
    ) : null

  return (
    <WizardModal
      backdropClose={!submitting}
      closeDisabled={submitting}
      escClose={!submitting}
      footer={
        <CreationActionBar
          className="w-full"
          description={operationFeedback ?? (preview ? t.refinementDuration(preview.duration_seconds) : undefined)}
        >
          <button
            className={BTN_PRIMARY}
            disabled={submitting || !active || stale || !preview || (!feedback.trim() && !fitAspect)}
            onClick={() => void submit()}
            type="button"
          >
            {submitting ? t.submittingRefinement : t.startRefinement}
          </button>
          <button className={BTN_GHOST} disabled={submitting} onClick={onClose} type="button">
            {t.cancelUpload}
          </button>
        </CreationActionBar>
      }
      onClose={submitting ? () => undefined : onClose}
      regionId="desktop-life-refine-video"
      title={t.refineVideo}
      widthClass="max-w-2xl"
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted">{source === 'candidate' ? t.refinementCandidate : t.refinementCurrent}</p>
        <DesktopLifePreview action={action} draft={source === 'candidate'} set={set} />
        <p className="text-sm leading-relaxed text-body">{t.refineVideoHint}</p>
        <label className="flex flex-col gap-2 text-sm text-body">
          {t.refineVideoFeedback}
          <textarea
            className={`${INPUT_CLASS} min-h-24 resize-y`}
            disabled={submitting}
            maxLength={600}
            onChange={event => setFeedback(event.target.value)}
            placeholder={t.refineVideoExample}
            rows={3}
            value={feedback}
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-body">
          <input
            checked={fitAspect}
            disabled={submitting}
            onChange={event => setFitAspect(event.target.checked)}
            type="checkbox"
          />
          {t.fitVideoAspect}
        </label>
      </div>
    </WizardModal>
  )
}
