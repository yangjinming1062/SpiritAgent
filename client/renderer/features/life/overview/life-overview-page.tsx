import { useStore } from '@nanostores/react'
import { useLayoutEffect, useRef } from 'react'

import { $appNavigationRevision, $appNavigationTarget, consumeLifePursuitFocus } from '@/app/navigation/app-navigation'
import { requestResource } from '@/shared/lib/resource-request'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { PanelSelect } from '@/ui'

import { LifeReader } from '../../../../../shared/life-reader'
import styles from '../life.module.css'
import { $lifeRevision } from '../store'

import { DesktopLifeMediaTile } from './life-media'
import { LifeResultLinks } from './life-results'
import { $lifeUi, $lifeUiRestoreRevision, enterLifeOverview, updateLifeUi } from './life-state'

const scrollContainer = (root: HTMLElement) => root.parentElement

export function LifeOverviewPage({ visible = true }: { visible?: boolean }) {
  const auth = useStore($auth)
  const gateway = useStore($gatewayState)
  const revision = useStore($lifeRevision)
  const state = useStore($lifeUi)
  const restore = useStore($lifeUiRestoreRevision)
  const target = useStore($appNavigationTarget)
  const navigation = useStore($appNavigationRevision)
  const locale = useStore($locale)
  const entered = useRef<{ navigation: number; restore: number } | null>(null)

  useLayoutEffect(() => {
    if (
      visible &&
      target.location.page === 'life' &&
      target.location.section === 'now' &&
      (entered.current?.navigation !== navigation || entered.current.restore !== restore)
    ) {
      entered.current = { navigation, restore }
      enterLifeOverview(navigation, target.focus.page === 'life' ? target.focus.pursuitId : undefined)
      consumeLifePursuitFocus()
    }
  }, [target, navigation, restore, visible])

  return (
    <div className={styles.page}>
      <LifeReader
        getScroller={scrollContainer}
        key={auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind}
        locale={locale}
        mediaTile={DesktopLifeMediaTile}
        read={requestResource}
        restoreRevision={restore}
        results={ref => <LifeResultLinks reference={ref} />}
        revision={gateway + '/' + revision + '/' + restore}
        setState={updateLifeUi}
        state={state}
        statusSelect={props => <PanelSelect {...props} widthClass="w-32 max-w-full" />}
        visible={visible && auth.kind === 'authenticated'}
      />
    </div>
  )
}
