import { useStore } from '@nanostores/react'
import type React from 'react'

import { $auth } from '@/shared/store/auth'

// 账户切换时按 accountId 重挂载根组件，释放旧账户的组件状态；未鉴权阶段以状态名为 key。
export function AccountScopedRoot({ RootComponent }: { RootComponent: React.ComponentType }): React.JSX.Element {
  const auth = useStore($auth)
  const key = auth.kind === 'authenticated' ? auth.snapshot.accountId : auth.kind

  return <RootComponent key={key} />
}
