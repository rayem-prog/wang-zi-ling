import { client } from './client.js'

export const settingsApi = {
  getSettings: () => client.get('/settings').then(res => res.data),
  updateSettings: (data) => client.post('/settings', data).then(res => res.data),
  testNotify: (data) => client.post('/settings/notify/test', data).then(res => res.data),
  stopDaemon: () => client.post('/settings/daemon/stop').then(res => res.data),
  startDaemon: () => client.post('/settings/daemon/start').then(res => res.data),
}
