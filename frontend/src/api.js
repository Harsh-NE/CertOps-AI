import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// An expired or invalid token sends the user back to the login screen.
api.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error.response?.status === 401 && localStorage.getItem('token')) {
      localStorage.removeItem('token')
      window.dispatchEvent(new Event('storage'))
    }
    return Promise.reject(error)
  },
)

export default api
