import { client } from './client.js'
import { runSandboxJob, waitSandboxJob } from './sandboxJobs.js'

export const sandboxApi = {
  runSandboxJob, waitSandboxJob,
  getCurrentSandboxJob: () => client.get('/sandbox/jobs/current').then(res => res.data),
  getSandboxSchemes: () => client.get('/sandbox/schemes').then(res => res.data),
  seedSandboxData: (force = false) => client.post(`/sandbox/seed?force=${force}`).then(res => res.data),
  initSandbox: (data) => client.post('/sandbox/init', data).then(res => res.data),
  stepSandbox: (days = 1) => client.post('/sandbox/step', { days }).then(res => res.data),
  fastForwardSandbox: () => client.post('/sandbox/fast-forward').then(res => res.data),
  getSandboxStatus: () => client.get('/sandbox/status').then(res => res.data),
  compareSandboxSchemes: (data = {}) => client.post('/sandbox/compare', data).then(res => res.data),
}
