import { signalsApi } from './signals.js'
import { marketApi } from './market.js'
import { holdingsApi } from './holdings.js'
import { paperApi } from './paper.js'
import { recapApi } from './recap.js'
import { settingsApi } from './settings.js'
import { sandboxApi } from './sandbox.js'

// Stable public API; views may also import a domain group directly.
export const api = {
  ...signalsApi,
  ...marketApi,
  ...holdingsApi,
  ...paperApi,
  ...recapApi,
  ...settingsApi,
  ...sandboxApi,
}
