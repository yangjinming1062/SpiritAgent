import { execFile } from 'node:child_process'

export interface NativeDisplayBounds {
  x: number
  y: number
  width: number
  height: number
}

function validBounds(value: unknown): value is NativeDisplayBounds {
  if (!value || typeof value !== 'object') {
    return false
  }

  const bounds = value as Partial<NativeDisplayBounds>

  return (
    [bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isSafeInteger) &&
    typeof bounds.x === 'number' &&
    Math.abs(bounds.x) <= 1_000_000 &&
    typeof bounds.y === 'number' &&
    Math.abs(bounds.y) <= 1_000_000 &&
    typeof bounds.width === 'number' &&
    bounds.width > 0 &&
    bounds.width <= 65_536 &&
    typeof bounds.height === 'number' &&
    bounds.height > 0 &&
    bounds.height <= 65_536
  )
}

/** Native monitor bounds recover pixels lost when Electron rounds physical sizes to DIP. */
export async function readNativeDisplayBounds(
  helperPath: string,
  requested: NativeDisplayBounds[]
): Promise<(NativeDisplayBounds | null)[]> {
  if (requested.length > 32 || requested.some(bounds => !validBounds(bounds))) {
    throw new Error('Invalid native display bounds request')
  }

  const output = await new Promise<string>((resolve, reject) => {
    execFile(
      helperPath,
      ['display-bounds', JSON.stringify(requested)],
      {
        windowsHide: true,
        timeout: 5_000,
        maxBuffer: 65_536,
        encoding: 'utf8'
      },
      (error, stdout) => {
        if (error) {
          reject(error)
        } else {
          resolve(stdout)
        }
      }
    )
  })

  const parsed: unknown = JSON.parse(output)

  if (
    !parsed ||
    typeof parsed !== 'object' ||
    !('event' in parsed) ||
    parsed.event !== 'display_bounds' ||
    !('displays' in parsed)
  ) {
    throw new Error('Invalid native display bounds response')
  }

  const results = parsed.displays

  if (!Array.isArray(results) || results.length !== requested.length) {
    throw new Error('Invalid native display bounds count')
  }

  return results.map((bounds: unknown, index: number) => {
    if (bounds === null) {
      return null
    }

    const original = requested[index]

    if (
      !validBounds(bounds) ||
      Math.abs(bounds.x - original.x) > 2 ||
      Math.abs(bounds.y - original.y) > 2 ||
      Math.abs(bounds.width - original.width) > 2 ||
      Math.abs(bounds.height - original.height) > 2
    ) {
      throw new Error('Native display bounds do not match the requested monitor')
    }

    return bounds
  })
}
