import type React from 'react'

import { useStrings } from '@/shared/strings'
import { Badge, BTN_SUBTLE, SettingRow } from '@/ui'
import { MediaReviewButton } from '@/ui/media-review-button'

import { visualJobCommands } from '../../../../../shared/visuals'

import type { VisualJob, VisualJobCommand } from './types'
import type { VisualsCopy } from './visual-labels'
import { visualJobLabel } from './visual-labels'

/** 库页的进行中区块与影像详情共用，改动要同时覆盖两处。 */
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

  return (
    <SettingRow
      description={
        <span className="inline-flex flex-wrap items-center gap-1.5">
          <Badge>{job.video_queue ? queueText[job.video_queue.state] : (t.jobState[job.state] ?? job.state)}</Badge>
          {job.error ? <span>{job.error}</span> : null}
        </span>
      }
      label={visualJobLabel(job, t)}
    >
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
    </SettingRow>
  )
}
