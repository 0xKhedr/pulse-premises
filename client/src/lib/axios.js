import axios from 'axios'
import Cookies from 'js-cookie'

const TTN_API_URL = import.meta.env.VITE_TTN_API_URL;

export const ttn_api = axios.create({
  baseURL: TTN_API_URL, 
});
ttn_api.interceptors.request.use((config) => {
  const ttn_api_key = Cookies.get('ttn_api_key');
  if (ttn_api_key) config.headers.Authorization = `Bearer ${ttn_api_key}`;

  return config;
});
