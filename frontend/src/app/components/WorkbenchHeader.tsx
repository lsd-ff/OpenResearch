import { useEffect, useState } from 'react';
import { Menu, Network, SlidersHorizontal } from 'lucide-react';
import { API_BASE_URL } from '../../config/env';
import { ApiConnection } from '../../features/research/ApiConnection';

interface Props {
  sessionsOpen: boolean;
  onToggleSessions: () => void;
  onOpenResearch: () => void;
}

export function WorkbenchHeader({ sessionsOpen, onToggleSessions, onOpenResearch }: Props) {
  const [connection, setConnection] = useState('正在连接');
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => { controller.abort(); setConnection('服务未连接'); }, 8000);
    void fetch(`${API_BASE_URL}/ping`, { signal: controller.signal, credentials: 'include' })
      .then(response => setConnection(response.ok ? '服务已连接' : '服务不可用'))
      .catch(() => { if (!controller.signal.aborted) setConnection('服务未连接'); })
      .finally(() => window.clearTimeout(timeout));
    return () => { controller.abort(); window.clearTimeout(timeout); };
  }, []);

  return <header className="workbench-header">
    <button className="icon-button session-toggle" onClick={onToggleSessions}
      aria-label="打开会话导航" aria-expanded={sessionsOpen}><Menu size={20} /></button>
    <a className="workbench-brand" href="#" aria-label="OpenResearch 科研工作台">
      <Network aria-hidden="true" /><span>OpenResearch</span>
    </a>
    <div className="workbench-tools" role="toolbar" aria-label="工作台设置">
      <span className="connection-status" data-online={connection === '服务已连接'} role="status"><span />{connection}</span>
      <ApiConnection />
      <button className="research-launch" onClick={onOpenResearch}><SlidersHorizontal size={16} /><span>高级实验设置</span></button>
    </div>
  </header>;
}
