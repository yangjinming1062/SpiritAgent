export function chatDisplayText(text: string, streaming = false): string {
  const visible = text.replace(/\bMEDIA:[^\s]*/g, '')

  // 增量可能停在标记中间，等后续字符确认后再显示普通台词。
  return streaming ? visible.replace(/\b(?:M|ME|MED|MEDI|MEDIA)$/, '') : visible
}
