import { registerEntryIpc } from './entry/ipc'
import { createEntryServices } from './entry/services'

const services = createEntryServices()
const authActions = registerEntryIpc(services)

void services.start(authActions)
