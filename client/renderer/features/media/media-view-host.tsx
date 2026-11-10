import { createContext, type ReactNode, useContext } from 'react'

// 查看器按承载视图归属：同进程多个窗口或页面并存时，预览只在当前视图的查看器里打开。
const MediaViewHostContext = createContext<string | undefined>(undefined)

export function MediaViewHost({ children, viewId }: { children: ReactNode; viewId?: string }): React.JSX.Element {
  return <MediaViewHostContext.Provider value={viewId}>{children}</MediaViewHostContext.Provider>
}

export function useMediaViewId(): string | undefined {
  return useContext(MediaViewHostContext)
}
