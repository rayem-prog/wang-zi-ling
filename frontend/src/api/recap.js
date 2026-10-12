import { client } from './client.js'

export const recapApi = {
  getRecapList: () => client.get('/recap/list').then(res => res.data),
  getRecapReport: (date) => client.get(`/recap/${date}`).then(res => res.data),
  runDailyRecap: (fastMode = false) => client.post(`/recap/run?fast_mode=${fastMode}`).then(res => res.data),
  getRecapJob: (jobId) => client.get(`/recap/jobs/${encodeURIComponent(jobId)}`).then(res => res.data),
  getRecapSummary: () => client.get('/recap/status/summary').then(res => res.data),
}
