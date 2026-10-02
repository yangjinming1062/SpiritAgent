import { createContext, type ReactNode, useContext } from 'react'

const PanelActivityContext = createContext(true)

// 同一 renderer 内的多个面板共享 window 事件；Portal 保留此上下文。
export function PanelActivityProvider({
  active,
  children
}: {
  active: boolean
  children: ReactNode
}): React.JSX.Element {
  const parentActive = useContext(PanelActivityContext)

  return <PanelActivityContext value={parentActive && active}>{children}</PanelActivityContext>
}

export function usePanelActivity(): boolean {
  return useContext(PanelActivityContext)
}
