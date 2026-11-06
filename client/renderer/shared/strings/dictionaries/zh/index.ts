import { accounts } from './accounts'
import { actions } from './actions'
import { boot } from './boot'
import { chat } from './chat'
import { common } from './common'
import { companion } from './companion'
import { creation } from './creation'
import { desktop } from './desktop'
import { life } from './life'
import { living } from './living'
import { settings } from './settings'
import { voice } from './voice'

export const dict = {
  ...life,
  ...common,
  ...creation,
  ...actions,
  ...boot,
  ...voice,
  ...settings,
  ...chat,
  ...companion,
  ...living,
  ...desktop,
  ...accounts
}

export type Dictionary = typeof dict
