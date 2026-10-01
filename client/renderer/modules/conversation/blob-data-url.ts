/** 读取 Blob 为 data URL；读取失败时拒绝。 */
export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    const fail = (): void => reject(new Error('read failed'))

    reader.onload = () => (typeof reader.result === 'string' ? resolve(reader.result) : fail())
    reader.onerror = fail
    reader.readAsDataURL(blob)
  })
}
