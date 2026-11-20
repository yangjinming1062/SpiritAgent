import type { ReactElement } from 'react'
import { useState } from 'react'

import { ApiError } from './api'
import { usePageState } from './page-state'
import { useAlive } from './tasks'

export function useAssetSubmission(
  key: string,
  onCheck: () => Promise<void>
): {
  check: () => Promise<void>
  clearSubmission: () => void
  reconciled: boolean
  submit: <Result>(write: () => Promise<Result>, apply?: (result: Result) => void | Promise<void>) => Promise<void>
  submitted: boolean
} {
  const alive = useAlive()
  const [submitted, setSubmitted] = usePageState(`${key}.submitted`, false)
  const [reconciled, setReconciled] = useState(false)

  const submit = async <Result,>(write: () => Promise<Result>, apply?: (result: Result) => void | Promise<void>) => {
    setSubmitted(true)
    setReconciled(false)
    let result: Result
    try {
      result = await write()
    } catch (failure) {
      if (
        alive() &&
        failure instanceof ApiError &&
        failure.status !== null &&
        failure.status >= 400 &&
        failure.status < 500 &&
        failure.status !== 408
      ) {
        setSubmitted(false)
      }
      throw failure
    }
    // 已受理的写入只等待实体落地或权威回读，读失败时保留核对入口。
    const refresh = apply ? apply(result) : alive() && onCheck()
    if (refresh) {
      await refresh
    }
    if (alive()) {
      setSubmitted(false)
    }
  }
  const check = async () => {
    setReconciled(false)
    await onCheck()
    if (alive()) {
      setReconciled(true)
    }
  }

  return { check, clearSubmission: () => setSubmitted(false), reconciled, submit, submitted }
}

export function AssetResultRecovery({
  onCheck,
  onContinue,
  reconciled
}: {
  onCheck: () => Promise<void>
  onContinue: () => void
  reconciled: boolean
}): ReactElement {
  return (
    <div className="creation-status">
      <p>上次制作的结果尚未确认。请先刷新，核对原来的结果。</p>
      <div className="button-row">
        <button onClick={onCheck} type="button">
          刷新结果
        </button>
        <button disabled={!reconciled} onClick={onContinue} type="button">
          已核对，继续调整
        </button>
      </div>
    </div>
  )
}
