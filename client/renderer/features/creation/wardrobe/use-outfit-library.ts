import { useStore } from '@nanostores/react'
import { useLayoutEffect, useRef, useState } from 'react'

import { log } from '@/shared/lib/log'
import { currentClearEpoch } from '@/shared/lib/storage'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { $outfitPolicy, $outfits, $wardrobeStatus, deleteOutfit, retryOutfitDescription, setOutfitPolicy } from './wardrobe-store'

/** 衣柜列表与行级操作：读取、分页滚动恢复、删除与描述重试、政策开关。 */
export function useOutfitLibrary({ restoreKey }: { restoreKey: boolean }) {
  const outfits = useStore($outfits)
  const wardrobeStatus = useStore($wardrobeStatus)
  const outfitPolicy = useStore($outfitPolicy)
  const t = useStrings().living.outfit
  const [busyId, setBusyId] = useState<string | null>(null)
  const [policyBusy, setPolicyBusy] = useState(false)
  const libraryRef = useRef<HTMLDivElement>(null)
  const listScrollRef = useRef(0)

  // 进入列表时恢复上次离开的滚动位置；滚动写入按帧合并。
  useLayoutEffect(() => {
    const element = libraryRef.current

    if (element) {
      element.scrollTop = listScrollRef.current
    }

    let frame = 0

    const savePosition = (): void => {
      if (frame !== 0) {
        return
      }

      frame = window.requestAnimationFrame(() => {
        frame = 0
        listScrollRef.current = element?.scrollTop ?? 0
      })
    }

    element?.addEventListener('scroll', savePosition)

    return () => {
      element?.removeEventListener('scroll', savePosition)

      if (frame !== 0) {
        window.cancelAnimationFrame(frame)
        frame = 0
      }
    }
  }, [restoreKey])

  const withBusy = (id: string, action: () => Promise<unknown>): void => {
    const epoch = currentClearEpoch()
    setBusyId(id)
    void action().finally(() => {
      if (epoch === currentClearEpoch()) {
        setBusyId(null)
      }
    })
  }

  const completeDescription = (id: string): void => {
    const epoch = currentClearEpoch()

    withBusy(id, async () => {
      try {
        await retryOutfitDescription(id)
      } catch (err) {
        log.warn('outfit', 'retry description failed', err)

        if (epoch === currentClearEpoch()) {
          notifyError(err, t.descriptionRetryFailed)
        }
      }
    })
  }

  const removeOutfit = (id: string): void => {
    withBusy(id, async () => {
      try {
        await deleteOutfit(id)
      } catch (err) {
        log.warn('outfit', 'delete failed', err)
        notifyError(err, t.deleteFailed)
      }
    })
  }

  const togglePolicy = async (): Promise<void> => {
    setPolicyBusy(true)
    const epoch = currentClearEpoch()
    const next = outfitPolicy === 'locked' ? 'llm_may_replace' : 'locked'
    const saved = await setOutfitPolicy(next)
    setPolicyBusy(false)

    if (!saved && epoch === currentClearEpoch()) {
      notify({ kind: 'error', message: t.policyFailed, title: t.policyFailed })
    }
  }

  return {
    busyId,
    completeDescription,
    libraryRef,
    outfitPolicy,
    outfits,
    policyBusy,
    removeOutfit,
    togglePolicy,
    wardrobeStatus
  }
}
