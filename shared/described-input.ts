/** 描述整理的唯一拼装点：空段丢弃，多段按行连接；整理成功后调用方清空补充输入。 */
export function composeDescribedInput(description: string, refinement: string): string {
  return [description.trim(), refinement.trim()].filter(Boolean).join('\n')
}
