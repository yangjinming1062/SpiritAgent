// 附件指令只用于请求正文；显示与编辑共用逐行过滤，保留普通 @ 提及。
export function stripAttachmentDirectives(text: string): string {
  return text
    .split('\n')
    .filter(line => !/^@(file|folder):/i.test(line.trim()))
    .join('\n')
}
