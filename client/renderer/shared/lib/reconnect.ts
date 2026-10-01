const BASE_MS = 1_000
const MAX_DELAY_MS = 15_000
const JITTER_RATIO = 0.5

export function reconnectBackoffMs(attempt: number): number {
  const base = Math.min(MAX_DELAY_MS, BASE_MS * 2 ** attempt)
  const jitter = Math.random() * base * JITTER_RATIO

  return Math.min(MAX_DELAY_MS, base + jitter)
}
