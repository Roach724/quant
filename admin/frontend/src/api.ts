import axios from 'axios';

const API_BASE = '';
const WS_PROTO = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
export const WS_BASE = `${WS_PROTO}//${window.location.host}`;

export const api = {
  get: (path: string) => axios.get(`${API_BASE}${path}`).then((r) => r.data),
  post: (path: string, data?: any) => axios.post(`${API_BASE}${path}`, data).then((r) => r.data),
  put: (path: string, data?: any) => axios.put(`${API_BASE}${path}`, data).then((r) => r.data),
};
