import { useStore } from '@nanostores/react'
import type React from 'react'

import { useStrings } from '@/shared/strings'
import { ErrorBanner, LoadingBlock } from '@/ui'

import styles from '../life.module.css'
import { $lifeError, $lifeLoading, $lifeState, refreshLife } from '../store'

/** 近况叙事只展示后端叙事与中性状态；加载失败保留旧内容并可重试。 */
export function LifeNarrative(): React.JSX.Element {
  const state = useStore($lifeState)
  const loading = useStore($lifeLoading)
  const failed = useStore($lifeError)
  const strings = useStrings()
  const t = strings.life

  return (
    <>
      {!state && loading ? (
        <LoadingBlock label={t.loading} />
      ) : (
        <p className={styles.activity}>{state?.narrative || t.unavailable}</p>
      )}
      {failed && (
        <ErrorBanner message={t.failed} onRetry={() => void refreshLife()} retryLabel={strings.common.retry} />
      )}
    </>
  )
}
