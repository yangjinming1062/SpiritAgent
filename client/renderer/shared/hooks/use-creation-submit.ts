import { useCallback, useRef, useState } from 'react'

/**
 * 创建提交守卫：防重入 + 修订计数 + 提交期间草稿变更检测。
 *
 * 修订计数用于「提交中用户又改了草稿」的判定：发起时记录 revision，
 * 完成回执时 revision 不一致则丢弃成功后的清草稿/跳转动作。
 * 结束时无论修订是否变化都复位忙碌位，避免悬挂。
 */
export function useCreationSubmit() {
  const [submitting, setSubmitting] = useState(false)
  const submittingRef = useRef(false)
  const revisionRef = useRef(0)

  /** 草稿发生变化时调用；使在途提交的成功回执失效。 */
  const bumpRevision = useCallback((): void => {
    revisionRef.current += 1
  }, [])

  const currentRevision = useCallback((): number => revisionRef.current, [])

  const beginSubmit = useCallback((isBlocked: () => boolean = () => false): boolean => {
    if (submittingRef.current || isBlocked()) {
      return false
    }

    submittingRef.current = true
    setSubmitting(true)

    return true
  }, [])

  /** 复位忙碌位；返回 false 表示提交期间草稿已变化，成功动作应丢弃。 */
  const endSubmit = useCallback((startedRevision: number): boolean => {
    const unchanged = revisionRef.current === startedRevision

    submittingRef.current = false
    setSubmitting(false)

    return unchanged
  }, [])

  return { submitting, bumpRevision, currentRevision, beginSubmit, endSubmit }
}
