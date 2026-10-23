import axios from 'axios'
import { ElMessage } from 'element-plus'

/** 统一 axios 封装：baseURL=/api（开发经 Vite 代理到 8000），响应直接取 data。 */
const request = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

request.interceptors.response.use(
  (resp) => resp.data,
  (error) => {
    const resp = error.response
    const detail = resp?.data?.detail
    let msg: string
    if (typeof detail === 'string') {
      msg = detail
    } else if (Array.isArray(detail) && detail.length) {
      // FastAPI 422 校验错误：detail 是错误对象数组 → 转成可读提示
      msg = detail
        .map((d: any) => d?.msg || '')
        .filter(Boolean)
        .slice(0, 3)
        .join('；')
      if (!msg) msg = `请求参数校验失败（${resp.status}）`
    } else if (!resp) {
      msg = '无法连接后端服务：请双击 start.bat 启动后端（8000 端口）'
    } else if (resp.status >= 500) {
      msg = `后端服务异常（${resp.status}）：请检查后端窗口是否被关闭，必要时双击 start.bat 重启`
    } else {
      msg = error.message || '请求失败'
    }
    ElMessage.error(msg)
    return Promise.reject(error)
  },
)

export default request
