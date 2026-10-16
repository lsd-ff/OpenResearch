import { isAxiosError } from 'axios';

export const apiErrorMessage = (error: unknown, fallback: string): string => {
  if (!isAxiosError<{ error?: unknown }>(error)) return fallback;
  const message = error.response?.data?.error;
  if (typeof message !== 'string' || !message.trim()) return fallback;
  if (message.includes('LLM model and API key must be configured')) {
    return '模型尚未配置，请设置模型名称、接口地址和 API Key。';
  }
  if (message.startsWith('model provider')) {
    if (message.includes('HTTP 401') || message.includes('HTTP 403')) {
      return '模型服务鉴权失败，请检查 API Key、模型权限和接口地址。';
    }
    if (message.includes('HTTP 429')) return '模型服务请求过于频繁，请稍后重试。';
    if (message.includes('timed out')) return '模型响应超时，请重试或调整模型请求预算。';
    if (message.includes('response contract') || message.includes('structured response')) {
      return '模型返回的数据格式不符合要求，请重试。';
    }
  }
  if (message.startsWith('model output was truncated')) {
    return '模型输出被截断或拒绝，可简化任务或调整输出上限后重试。';
  }
  // 服务端只返回已脱敏的公开错误；保留资产、归属和预算错误的具体处理线索。
  return message.slice(0, 600);
};
