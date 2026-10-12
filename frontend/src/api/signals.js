import { client } from './client.js'

export const signalsApi = {
  refreshSignals: () => client.post('/signals/refresh').then(res => res.data),
  getSignals: (params = {}) => client.get('/signals', { params }).then(res => res.data),
  getSignalsStats: () => client.get('/signals/stats').then(res => res.data),
  getWatchlist: () => client.get('/signals/watchlist').then(res => res.data),
  addToWatchlist: (data) => client.post('/signals/watchlist', data).then(res => res.data),
  removeFromWatchlist: (code) => client.delete(`/signals/watchlist/${code}`).then(res => res.data),
  searchStocks: (q) => client.get(`/stock/search?q=${encodeURIComponent(q)}`).then(res => res.data),
}
