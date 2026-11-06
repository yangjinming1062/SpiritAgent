import type { DesktopJumpListTask } from '../../shared/desktop-jump-list'

export function parseJumpListTasks(value: unknown): DesktopJumpListTask[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Desktop helper returned invalid Jump List tasks')
  }

  const tasks = (value as { tasks?: unknown }).tasks

  if (!Array.isArray(tasks) || tasks.length > 64) {
    throw new Error('Desktop helper returned invalid Jump List task count')
  }

  return tasks.map(raw => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error('Desktop helper returned invalid Jump List task')
    }

    const task = raw as Partial<DesktopJumpListTask>

    if (
      typeof task.id !== 'string' ||
      task.id.length < 1 ||
      task.id.length > 512 ||
      typeof task.title !== 'string' ||
      task.title.length < 1 ||
      task.title.length > 255 ||
      typeof task.description !== 'string' ||
      task.description.length > 2048 ||
      typeof task.iconPath !== 'string' ||
      task.iconPath.length > 32_768 ||
      typeof task.iconIndex !== 'number' ||
      !Number.isInteger(task.iconIndex) ||
      task.iconIndex < -2_147_483_648 ||
      task.iconIndex > 2_147_483_647
    ) {
      throw new Error('Desktop helper returned invalid Jump List task fields')
    }

    return {
      id: task.id,
      title: task.title,
      description: task.description,
      iconPath: task.iconPath,
      iconIndex: task.iconIndex
    }
  })
}

export function isValidJumpListTarget(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= 32_768
}

export function isValidJumpListTaskId(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= 512
}
