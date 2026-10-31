import type React from 'react'

import type { VisualJob, VisualJobCommand } from '@/modules/visuals'
import { Badge, BTN_SUBTLE, SettingRow } from '@/shared/panel'

import { visualJobCommands } from '../../../../../shared/visuals'

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
          <Badge>{t.jobState[job.state] ?? job.state}</Badge>
          {job.error ? <span>{job.error}</span> : null}
        </span>
      }
      label={visualJobLabel(job, t)}
    >
      <div className="flex flex-wrap gap-2">
        {visualJobCommands(job.state, job.failure_reason).map(command => (
          <button className={BTN_SUBTLE} disabled={busy} key={command} onClick={() => onCommand(command)} type="button">
            {labels[command]}
          </button>
        ))}
      </div>
    </SettingRow>
  )
}
