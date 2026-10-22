import { useState } from 'react';
import { FileText, FolderOpen, LogIn, Plus, Search, Settings2, UserRound, X } from 'lucide-react';

interface SessionSummary { id: string; title: string; updatedAt: string; messageCount: number }
export interface SessionManagerState {
  isLoggedIn: boolean; userId: string | null; loginInput: string;
  activeSessionId: string | null; sessions: SessionSummary[]; loading: boolean;
}
export interface SessionManagerActions {
  setLoginInput: (value: string) => void; onLogin: () => void;
  onCreateSession: () => void; onSwitchSession: (sessionId: string) => void;
}
interface Props {
  state: SessionManagerState; actions: SessionManagerActions;
  open: boolean; onClose: () => void;
}

function formatTime(value: string) {
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? '' : time.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
}

export function ChatSessionManager({ state, actions, open, onClose }: Props) {
  const [search, setSearch] = useState('');
  const [identityOpen, setIdentityOpen] = useState(false);
  const sessions = state.sessions.filter(session => session.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  function createSession() { actions.onCreateSession(); setSearch(''); onClose(); }
  return <>
    {open && <button className="session-backdrop" aria-label="收起会话导航" onClick={onClose} />}
    <aside className={`session-sidebar ${open ? 'is-open' : ''}`} aria-label="会话导航">
      <div className="session-sidebar-heading"><span>工作空间</span><button className="icon-button" aria-label="关闭会话导航" onClick={onClose}><X size={18} /></button></div>
      <button className="new-session-button" disabled={state.loading} onClick={createSession}
        aria-label={state.isLoggedIn ? '新建 Session' : '新建临时 Session'}><Plus size={20} />新建会话</button>
      <label className="session-search"><Search size={16} /><input value={search}
        onChange={event => setSearch(event.target.value)} placeholder="搜索会话" aria-label="搜索会话" /></label>
      <div className="session-list-heading"><span>最近会话</span><span>{state.sessions.length}</span></div>
      <div role="region" aria-label="历史会话" className="session-list">
        {sessions.map(session => <button key={session.id} className={`session-item ${session.id === state.activeSessionId ? 'is-active' : ''}`}
          aria-current={session.id === state.activeSessionId ? 'page' : undefined}
          onClick={() => { actions.onSwitchSession(session.id); onClose(); }} title={session.title}>
          <FileText size={18} /><span className="session-item-copy"><strong>{session.title}</strong>
            <span>{session.messageCount} 条消息<span aria-hidden="true"> · </span>{formatTime(session.updatedAt)}</span></span>
        </button>)}
        {sessions.length === 0 && <p className="session-empty">{search ? '没有匹配的会话' : '创建会话，开始新的研究'}</p>}
      </div>
      <div className="session-identity">
        <button className="workspace-identity-toggle" onClick={() => setIdentityOpen(!identityOpen)} aria-expanded={identityOpen}>
          <FolderOpen size={20} /><span><strong>本地工作空间</strong><small>{state.isLoggedIn ? state.userId : '游客模式 · 会话保存在此浏览器'}</small></span><Settings2 size={16} />
        </button>
        {identityOpen && <div className="identity-settings">
          <p><UserRound size={14} />{state.isLoggedIn ? `当前用户：${state.userId}` : '设置固定用户 ID，关联已有研究记录。'}</p>
          {!state.isLoggedIn && <><input value={state.loginInput} onChange={event => actions.setLoginInput(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); actions.onLogin(); } }}
            placeholder="请输入用户 ID，例如 alice" aria-label="用户 ID" />
            <button onClick={actions.onLogin} disabled={state.loading || !state.loginInput.trim()}><LogIn size={14} />设置身份</button></>}
          <p className="identity-help">共享 API 令牌可在顶部连接设置中填写。</p>
        </div>}
      </div>
    </aside>
  </>;
}
