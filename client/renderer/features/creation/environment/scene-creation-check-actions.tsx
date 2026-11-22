import { useStrings } from '@/shared/strings'
import { BTN_PRIMARY, BTN_SUBTLE } from '@/ui'

import { finishSceneCreationWait, type SceneCreationIssue } from './scene'

export function SceneCreationCheckActions({
  issue,
  checking,
  onCheck
}: {
  issue: SceneCreationIssue
  checking: boolean
  onCheck: () => void
}): React.JSX.Element {
  const dict = useStrings()

  return (
    <>
      <button className={BTN_SUBTLE} disabled={checking} onClick={onCheck} type="button">
        {checking ? dict.common.loading : dict.living.scene.inspectLibrary}
      </button>
      {issue.inspected && (
        <button
          className={BTN_PRIMARY}
          disabled={checking}
          onClick={() => finishSceneCreationWait(issue)}
          type="button"
        >
          {dict.creation.finishWaiting}
        </button>
      )}
    </>
  )
}
