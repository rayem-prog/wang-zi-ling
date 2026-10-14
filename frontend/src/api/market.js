import { client } from './client.js'

export const marketApi = {
  getStatus: () => client.get('/status').then(res => res.data),
  retryMarketCollection: () => client.post('/status/refresh').then(res => res.data),
  getOrders: () => client.get('/orders').then(res => res.data),
  getOrdersExportUrl: () => '/api/orders/export',
  getMacro: () => client.get('/macro').then(res => res.data),
  getDailyKline: (code) => client.get(`/kline/daily/${code}`).then(res => res.data),
  getMinuteKline: (code) => client.get(`/kline/minute/${code}`).then(res => res.data),
  getIntradayDecisions: () => client.get('/intraday/decisions').then(res => res.data),
  getIntradayRadar: () => client.get('/intraday/radar').then(res => res.data),
}
