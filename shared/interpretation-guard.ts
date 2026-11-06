/**
 * 自然语言整理的异步守卫：请求代次 + 草稿修订标识。
 * 发起整理时冻结两者；描述、创作类型、编辑对象、编辑器开关或账户任何变化都推进草稿修订，
 * 响应回来时只有代次与修订都未变的结果才落地——旧响应不覆盖新输入，过期失败也不替换当前提示。
 */
export class InterpretationGuard {
  private request = 0
  private draft = 0

  /** 草稿任意变化（含换编辑对象、关编辑器、换账户）时调用，在途整理的响应即作废。 */
  revise(): void {
    this.draft += 1
  }

  /** 发起一次整理并记录当时的请求代次与草稿修订，返回响应回来时的落地校验。 */
  begin(): () => boolean {
    const request = ++this.request
    const draft = this.draft

    return () => request === this.request && draft === this.draft
  }
}
