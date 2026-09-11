import { type DependencyList, useEffect } from 'react'

type ListenerName = {
  [K in keyof Window['spiritagent']]: NonNullable<Window['spiritagent'][K]> extends (...args: never[]) => unknown
    ? K
    : never
}[keyof Window['spiritagent']]

// Caller-managed deps: the deps array is supplied by the caller and intentionally
// escapes both `react-hooks/exhaustive-deps` and React Compiler's static analysis.
/* eslint-disable react-hooks/exhaustive-deps, react-compiler/react-compiler */
export function useMainProcessListener<K extends ListenerName>(
  name: K,
  fn: Parameters<NonNullable<Window['spiritagent'][K]>>[0],
  deps: DependencyList
): void {
  useEffect(() => {
    const sub = window.spiritagent?.[name] as ((cb: typeof fn) => (() => void) | undefined) | undefined
    const off = sub?.(fn)

    return () => {
      off?.()
    }
  }, deps)
}
/* eslint-enable react-hooks/exhaustive-deps, react-compiler/react-compiler */
