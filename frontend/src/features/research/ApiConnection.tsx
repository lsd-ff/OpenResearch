import { useState } from 'react';
import { httpClient } from '../../services/api/httpClient';

export function ApiConnection() {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState('');
  const [message, setMessage] = useState('');
  async function connect() {
    try {
      await httpClient.post('/api/auth/session', {}, { headers: { Authorization: `Bearer ${token}` } });
      setToken(''); setMessage('已连接，令牌已交换为会话 Cookie。');
    } catch { setMessage('连接失败，请检查 API 地址和令牌。'); }
  }
  return <div className="api-connection"><button onClick={() => setOpen(!open)}>连接设置</button>
    {open && <section><label>API 令牌<input type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} /></label>
      <button onClick={() => void connect()}>连接</button><p role="status">{message}</p></section>}
  </div>;
}
