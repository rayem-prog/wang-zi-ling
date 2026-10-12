import { client } from './client.js'

export const paperApi = {
  getPaperAccount: () => client.get('/paper/account').then(res => res.data),
  placePaperOrder: (data) => client.post('/paper/order', data).then(res => res.data),
  makeRandomPortfolio: () => client.post('/paper/random-portfolio').then(res => res.data),
  shockPortfolio: () => client.post('/paper/shock-test').then(res => res.data),
  resetPaperAccount: () => client.post('/paper/reset').then(res => res.data),
  getAiSimulation: (riskPref = 'balanced') => client.get(`/paper/ai-simulation?risk_pref=${riskPref}`).then(res => res.data),
  executeAiSimulation: (data = {}) => client.post('/paper/ai-simulation/execute', data).then(res => res.data),
}
