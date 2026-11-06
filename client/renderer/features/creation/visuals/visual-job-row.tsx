import type React from 'react'

import { MediaReviewButton } from '@/features/media-review'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, CreationFeedback, type CreationFeedbackState } from '@/ui'

import { visualJobCommands } from '../../../../../shared/visuals'

import type { VisualJob, VisualJobCommand } from './types'
import type { VisualsCopy } from './visual-labels'
import { visualJobLabel } from './visual-labels'

/** 库页的进行中区块与影像详情共用；状态展示与其它创作业务同为 CreationFeedback。 */
export function VisualJobRow({
  busy,
  job,
  onCommand,
  t
}: {
  busy: boolean
  job: VisualJob
  onCommand: (command: VisualJobCommand) => void
  t: VisualsCopy
}): React.JSX.Element {
  const queueText = useStrings().common.videoQueue

  const labels: Record<VisualJobCommand, string> = {
    pause: t.jobPause,
    resume: job.failure_reason === 'result_download_failed' ? t.jobReceive : t.jobResume,
    cancel: t.jobCancel,
    retry: t.jobRetry
  }

  const state: CreationFeedbackState =
    job.stage === 'review_pending'
      ? 'review'
      : job.state === 'failed'
        ? 'error'
        : job.state === 'paused'
          ? 'paused'
          : job.state === 'cancelled'
            ? 'cancelled'
            : job.video_queue
              ? 'waiting'
              : 'working'

  const statusText = job.video_queue ? queueText[job.video_queue.state] : (t.jobState[job.state] ?? job.state)

  return (
    <CreationFeedback
      actions={
        <div className="flex flex-wrap gap-2">
          {job.stage === 'review_pending' ? <MediaReviewButton jobId={job.id} /> : null}
          {visualJobCommands(job.state, job.failure_reason)
            .filter(command => job.stage !== 'review_pending' || command === 'cancel')
            .map(command => (
              <button
                className={BTN_SUBTLE}
                disabled={busy}
                key={command}
                onClick={() => onCommand(command)}
                type="button"
              >
                {labels[command]}
              </button>
            ))}
        </div>
      }
      description={job.error ?? statusText}
      state={state}
      title={visualJobLabel(job, t)}
    />
  )
}
