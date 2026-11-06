import type { DesktopMediaBytes } from '@ipc/contracts'

/** 把主进程交付的媒体字节转成可播放对象地址；调用方负责 revoke。 */
export function createMediaObjectUrl(media: Pick<DesktopMediaBytes, 'bytes' | 'mime'>): string {
  return URL.createObjectURL(new Blob([new Uint8Array(media.bytes)], { type: media.mime }))
}
