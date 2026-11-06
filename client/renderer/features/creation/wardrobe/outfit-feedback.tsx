import type React from 'react'

import { BTN_SUBTLE } from '@/ui'

import type { OutfitSectionModel } from './use-outfit-section'

/** 结果未知时的两个显式动作：先核对，再由用户结束等待。 */
export function OutfitUnknownActions({ model }: { model: OutfitSectionModel }): React.JSX.Element {
  const { session, dict, common, setDesigning, wardrobeStatus } = model

  return (
    <>
      <button
        className={BTN_SUBTLE}
        disabled={wardrobeStatus === 'loading'}
        onClick={() => {
          setDesigning(false)
          void session.inspectResult()
        }}
        type="button"
      >
        {wardrobeStatus === 'loading' ? common.loading : dict.creation.checkResult}
      </button>
      {session.inspected && (
        <button
          className={BTN_SUBTLE}
          disabled={wardrobeStatus === 'loading'}
          onClick={session.finishWaiting}
          type="button"
        >
          {dict.creation.finishWaiting}
        </button>
      )}
    </>
  )
}
