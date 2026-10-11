/** helper 任务元数据；图标路径经主进程转换后才下发渲染层。 */
export interface DesktopJumpListTask {
  id: string
  title: string
  description: string
  iconPath: string
  iconIndex: number
}
