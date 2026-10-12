import { client } from './client.js'

export const holdingsApi = {
  getHoldings: () => client.get('/holdings').then(res => res.data),
  savePosition: (data) => client.post('/holdings', data).then(res => res.data),
  deletePosition: (code) => client.delete(`/holdings/${code}`).then(res => res.data),
  getPaper: () => client.get('/holdings/paper').then(res => res.data),
  createPaperOrder: (data) => client.post('/holdings/paper', data).then(res => res.data),
}
