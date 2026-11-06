import type { ReactElement } from 'react'

import type { RemoteApi, RemoteGateway } from './api'
import { Feed } from './Feed'
import { LifeSummary } from './LifeSummary'

/** 生活页签：近况在前，动态与日记沿用 Feed 自己的二级页签。 */
export function Life({
  api,
  gateway,
  onRead,
  refresh,
  unread
}: {
  api: RemoteApi
  gateway: RemoteGateway
  onRead: () => void
  refresh: number
  unread: { feed: boolean; diary: boolean }
}): ReactElement {
  return (
    <div className="page life-page">
      <LifeSummary api={api} refresh={refresh} />
      <Feed api={api} gateway={gateway} onRead={onRead} refresh={refresh} unread={unread} />
    </div>
  )
}
