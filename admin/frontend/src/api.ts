import axios from 'axios';

const API_BASE = 'http://localhost:8092';
export const WS_BASE = 'ws://localhost:8092';

export const api = {
  get: (path: string) => axios.get(`${API_BASE}${path}`).then((r) => r.data),
  post: (path: string, data?: any) =>
    axios.post(`${API_BASE}${path}`, data).then((r) => r.data),
  put: (path: string, data?: any) =>
    axios.put(`${API_BASE}${path}`, data).then((r) => r.data),
};
