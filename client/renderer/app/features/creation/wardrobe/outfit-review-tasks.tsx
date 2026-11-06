import { useEffect, useState } from 'react'
import type React from 'react'

import { requestResource } from '@/shared/lib/resource-request'
import { MediaReviewButton } from '@/shared/panel/media-review-button'

import type { ApiSchemas } from '../../../../../../shared/api.generated'

export function OutfitReviewTasks({ refreshKey }: { refreshKey: boolean }): React.JSX.Element | null {
  const [jobs, setJobs] = useState<ApiSchemas['MediaJobView'][]>([])
  const [error, setError] = useState('')
  const load = async (): Promise<void> => setJobs(await requestResource('media.get_outfit_review_jobs'))
  useEffect(() => {
    let current = true
    void requestResource('media.get_outfit_review_jobs')
      .then(value => {
        if (current) {
          setJobs(value)
        }
      })
      .catch(cause => {
        if (current) {
          setError(String(cause))
        }
      })

    return () => {
      current = false
    }
  }, [refreshKey])

  if (!jobs.length && !error) {
    return null
  }

  return (
    <div className="space-y-2 p-3" role="status">
      {error ? <p role="alert">{error}</p> : null}
      {jobs.map(job => (
        <div className="rounded-lg border border-line-standard p-3" key={job.id}>
          <p>{job.slots[0]?.error}</p>
          <MediaReviewButton jobId={job.id} onChanged={load} />
        </div>
      ))}
    </div>
  )
}
