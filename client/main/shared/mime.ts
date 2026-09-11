import path from 'node:path'

const MEDIA_MIME_TYPES: Record<string, string> = {
  '.avi': 'video/x-msvideo',
  '.bmp': 'image/bmp',
  '.flac': 'audio/flac',
  '.gif': 'image/gif',
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.m4a': 'audio/mp4',
  '.mkv': 'video/x-matroska',
  '.mov': 'video/quicktime',
  '.mp3': 'audio/mpeg',
  '.mp4': 'video/mp4',
  '.ogg': 'audio/ogg',
  '.opus': 'audio/ogg; codecs=opus',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.wav': 'audio/wav',
  '.webm': 'video/webm',
  '.webp': 'image/webp'
}

// 派生：MIME 以 audio/、video/ 或 model/ 开头的所有扩展名。
export const STREAMABLE_MEDIA_EXTS: Set<string> = new Set(
  Object.entries(MEDIA_MIME_TYPES)
    .filter(([, mime]) => mime.startsWith('audio/') || mime.startsWith('video/') || mime.startsWith('model/'))
    .map(([ext]) => ext)
)

export function mimeTypeForPath(filePath: string): string {
  const ext = path.extname(filePath || '').toLowerCase()

  return MEDIA_MIME_TYPES[ext] || 'application/octet-stream'
}

const EXT_FOR_MIME: Record<string, string> = {
  'image/bmp': '.bmp',
  'image/gif': '.gif',
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'image/svg+xml': '.svg',
  'image/webp': '.webp'
}

export function extensionForMimeType(mimeType: string): string {
  const type = String(mimeType || '')
    .split(';')[0]
    .trim()
    .toLowerCase()

  return EXT_FOR_MIME[type] || ''
}

export function dataUrlFromBuffer(buffer: Buffer | Uint8Array, mimeType: string): string {
  const buf = Buffer.isBuffer(buffer) ? buffer : Buffer.from(buffer)

  return `data:${mimeType};base64,${buf.toString('base64')}`
}

interface ParsedDataUrl {
  data: Buffer
  mime: string
}

// 解析 RFC 2397 `data:[<mediatype>][;base64],<payload>` 形式的 URL。
// 支持参数化媒体类型（如 audio/webm;codecs=opus）。
export function parseDataUrl(dataUrl: string): ParsedDataUrl {
  const raw = String(dataUrl || '').trim()
  const commaIdx = raw.indexOf(',')

  if (!raw.startsWith('data:') || commaIdx === -1) {
    throw new Error('Expected a base64 data URL')
  }

  const meta = raw.slice(5, commaIdx)
  const payload = raw.slice(commaIdx + 1)

  const parts = meta
    .split(';')
    .map(p => p.trim())
    .filter(Boolean)

  const isBase64 = parts.length > 0 && parts[parts.length - 1].toLowerCase() === 'base64'

  if (isBase64) {
    parts.pop()
  }

  const mime = (parts[0] || 'application/octet-stream').toLowerCase()
  const data = isBase64 ? Buffer.from(payload, 'base64') : Buffer.from(decodeURIComponent(payload), 'utf8')

  return { data, mime }
}

// 将 `data:<mime>[;base64],<payload>` 形式的 URL 解码回原始字节。
export function dataUrlToBuffer(dataUrl: string): Buffer {
  return parseDataUrl(dataUrl).data
}
