import { useEffect, useState } from 'react'
import type React from 'react'

import { MediaReviewButton } from '@/features/media-review'
import { requestResource } from '@/shared/lib/resource-request'
import { useStrings } from '@/shared/strings'
import { LibraryStatusBanner } from '@/ui'

import type { ApiSchemas } from '../../../../../shared/api.generated'

/** 造型评审任务条：与其它素材页同一状态条形态，只在有任务时出现。 */
export function OutfitReviewTasks({ refreshKey }: { refreshKey: boolean }): React.JSX.Element {
  const [jobs, setJobs] = useState<ApiSchemas['MediaJobView'][]>([])
  const [error, setError] = useState('')
  const t = useStrings().creation
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

  return (
    <LibraryStatusBanner
      candidates={[
        error ? { state: 'error' as const, description: error } : null,
        jobs.length > 0
          ? {
              state: 'review' as const,
              title: t.review,
              description: jobs.find(job => job.slots[0]?.error)?.slots[0]?.error,
              actions: jobs.map(job => <MediaReviewButton jobId={job.id} key={job.id} onChanged={load} />)
            }
          : null
      ]}
      className="px-3 pt-3"
    />
  )
}
