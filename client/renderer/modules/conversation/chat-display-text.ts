// 附件指令只用于请求正文；显示与编辑共用逐行过滤，保留普通 @ 提及。
export function stripAttachmentDirectives(text: string): string {
  return text
    .split('\n')
    .filter(line => !/^@(file|folder):/i.test(line.trim()))
    .join('\n')
}

export function chatDisplayText(text: string, streaming = false): string {
  const visible = text.replace(/\bMEDIA:[^\s]*/g, '')

  // 增量可能停在标记中间，等后续字符确认后再显示普通台词。
  return streaming ? visible.replace(/\b(?:M|ME|MED|MEDI|MEDIA)$/, '') : visible
}
