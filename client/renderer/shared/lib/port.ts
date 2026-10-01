/** 跨层注入的单实例端口：上层启动时 bind，使用方 get；未绑定即使用属于装配错误，直接抛出。 */
export function createPort<T>(name: string): { bind: (impl: T) => void; get: () => T } {
  let port: T | null = null

  return {
    bind: impl => {
      port = impl
    },
    get: () => {
      if (!port) {
        throw new Error(`${name} not bound — app bootstrap must initialize first`)
      }

      return port
    }
  }
}
