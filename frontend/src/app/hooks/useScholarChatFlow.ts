import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ChatMessage, IntentContext, PlanGraph, PlanResponse, UploadedFile } from '../../contracts/api';
import { getPlan, sendMessage, uploadFile } from '../../services/api/scholarApi';
import { apiErrorMessage } from '../../services/api/errors';
import { uiText } from '../constants/uiText';
import { codeSummary } from '../../features/execution/codeSummary';

interface UseScholarChatFlowOptions {
  onPlanGraphChanged: (planGraph: PlanGraph | null) => void;
}

interface ChatSessionRecord {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  chatHistory: ChatMessage[];
  activePlanId: string | null;
  intentContext: IntentContext | null;
  planGraph: PlanGraph | null;
  reportedPlanId?: string;
  reportedPlanStatus?: string;
}

interface PersistedChatState {
  userId: string | null;
  activeSessionId: string | null;
  sessions: ChatSessionRecord[];
}

interface SessionSummary {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messageCount: number;
}

const STORAGE_KEY = 'openresearch-python.chat-sessions.v1';

const createId = (prefix: string) => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
};

const createWelcomeMessage = (): ChatMessage => ({
  role: 'system',
  text: uiText.appWelcome,
});

const createSessionRecord = (index: number): ChatSessionRecord => {
  const now = new Date().toISOString();
  return {
    id: createId('session'),
    title: `新会话 ${index}`,
    createdAt: now,
    updatedAt: now,
    chatHistory: [createWelcomeMessage()],
    activePlanId: null,
    intentContext: null,
    planGraph: null,
  };
};

const createSessionState = (userId: string | null, sessionTitlePrefix: string): PersistedChatState => {
  const firstSession = createSessionRecord(1);
  return {
    userId,
    activeSessionId: firstSession.id,
    sessions: [{ ...firstSession, title: `${sessionTitlePrefix} 1` }],
  };
};

const normalizeChatState = (raw: unknown): PersistedChatState => {
  if (!raw || typeof raw !== 'object') {
    return { userId: null, activeSessionId: null, sessions: [] };
  }

  const data = raw as Partial<PersistedChatState>;
  const sessions = Array.isArray(data.sessions)
    ? data.sessions.filter((session): session is ChatSessionRecord => Boolean(session?.id))
      .map(session => ({ ...session, chatHistory: refreshWelcome(session.chatHistory) }))
    : [];

  return {
    userId: typeof data.userId === 'string' && data.userId.trim() ? data.userId.trim() : null,
    activeSessionId: typeof data.activeSessionId === 'string' && data.activeSessionId.trim() ? data.activeSessionId.trim() : null,
    sessions,
  };
};

const refreshWelcome = (messages: ChatMessage[]) => (Array.isArray(messages) ? messages : []).map((message, index) => {
  // 欢迎语是产品文案；只更新首条已知欢迎消息，保留用户对话和执行记录。
  const welcome = index === 0 && message.role === 'system' &&
    message.text.startsWith('你好，我是 OpenResearch 科研助手。');
  return welcome ? { ...message, text: uiText.appWelcome } : message;
});

const withPlanLink = (state: PersistedChatState): PersistedChatState => {
  const planId = new URLSearchParams(window.location.search).get('plan');
  if (!planId || !/^[a-f0-9]{32}$/.test(planId)) return state;
  const existing = state.sessions.find(session => session.activePlanId === planId);
  if (existing) return { ...state, activeSessionId: existing.id };
  // 链接只指定计划 ID；沿用当前身份，由服务端检查归属后恢复真实状态。
  const session = { ...createSessionRecord(state.sessions.length + 1),
    id: `plan-${planId}`, title: `执行记录 ${planId.slice(0, 8)}`, activePlanId: planId };
  return { ...state, activeSessionId: session.id, sessions: [...state.sessions, session] };
};

const loadPersistedChatState = (): PersistedChatState => {
  if (typeof window === 'undefined') {
    return createSessionState(null, '临时会话');
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return withPlanLink(createSessionState(null, '临时会话'));

    const normalized = normalizeChatState(JSON.parse(raw));
    if (normalized.sessions.length === 0 || !normalized.activeSessionId) {
      return withPlanLink(createSessionState(normalized.userId, '新会话'));
    }
    return withPlanLink(normalized);
  } catch {
    return withPlanLink(createSessionState(null, '临时会话'));
  }
};

const sanitizeUserId = (value: string) => value.trim().replace(/[^a-zA-Z0-9@._-]/g, '').slice(0, 128);

const deriveSessionTitle = (fallbackTitle: string, messages: ChatMessage[]): string => {
  const firstUserMessage = messages.find((message) => message.role === 'user' && message.text.trim());
  if (!firstUserMessage) return fallbackTitle;
  const compactText = firstUserMessage.text.replace(/\s+/g, ' ').trim();
  return compactText.length > 18 ? `${compactText.slice(0, 18)}...` : compactText;
};

const updateSessionRecord = (
  sessions: ChatSessionRecord[],
  sessionId: string,
  updater: (session: ChatSessionRecord) => ChatSessionRecord,
) => sessions.map((session) => (session.id === sessionId ? updater(session) : session));

export function useScholarChatFlow(options: UseScholarChatFlowOptions) {
  const { onPlanGraphChanged } = options;
  const [persistedState, setPersistedState] = useState<PersistedChatState>(() => loadPersistedChatState());
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
	const [pendingAttachments, setPendingAttachments] = useState<UploadedFile[]>([]);
	const [uploadingAttachments, setUploadingAttachments] = useState(false);
	const [attachmentError, setAttachmentError] = useState('');
  const [loginInput, setLoginInput] = useState(() => loadPersistedChatState().userId ?? '');
  const [guestUserId] = useState(() => {
    const key = 'openresearch-python.guest-user';
    const existing = window.localStorage.getItem(key);
    if (existing) return existing;
    const identifier = createId('guest');
    window.localStorage.setItem(key, identifier);
    return identifier;
  });

  const activeSession = useMemo(
    () => persistedState.sessions.find((session) => session.id === persistedState.activeSessionId) ?? null,
    [persistedState.activeSessionId, persistedState.sessions],
  );

  const chatHistory = activeSession?.chatHistory ?? [];
  const activePlanId = activeSession?.activePlanId ?? null;
	const activePlanStatus = activeSession?.planGraph?.status ?? null;
  const intentContext = useMemo<IntentContext | null>(() => {
    if (activeSession?.intentContext) return activeSession.intentContext;
    const graph = activeSession?.planGraph;
    return graph ? { raw_intent: graph.user_intent, intent_type: graph.intent_type,
      entities: {}, constraints: {}, metadata: {} } : null;
  }, [activeSession?.intentContext, activeSession?.planGraph]);
  const activeIdentity = persistedState.userId ?? guestUserId;

  useEffect(() => {
    if (!activePlanId || !activeSession?.id) return;
    const sessionId = activeSession.id;
    let canceled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function refreshPlan() {
      try {
        const response = await getPlan(activePlanId!, { userId: activeIdentity, sessionId });
        if (canceled) return;
        // 服务端状态为准；刷新浏览器、切回会话也能恢复已完成的图与证据。
        setPersistedState(previous => ({ ...previous, sessions: updateSessionRecord(previous.sessions, sessionId,
          session => {
            const graph = response.plan_graph;
            const terminal = ['completed', 'failed', 'canceled'].includes(graph.status ?? '');
            const alreadyReported = session.reportedPlanId === graph.id &&
              (session.reportedPlanStatus ?? session.planGraph?.status) === graph.status;
            if (!terminal || alreadyReported) return { ...session, planGraph: graph };
            const failure = graph.nodes.find(node => node.status === 'failed');
            const analysis = graph.nodes.find(node => node.type === 'framework_report')
              ?? graph.nodes.find(node => node.type === 'chat_answer')
              ?? graph.nodes.find(node => node.type === 'paper_analyze')
              ?? graph.nodes.find(node => node.type === 'code_execute');
            const report = analysis?.type === 'code_execute' ? codeSummary(graph, analysis) : analysis?.result;
            const validReport = analysis?.status === 'completed' && report &&
              (graph.status === 'completed' || analysis.trigger_rule === 'all_done');
            const text = graph.status === 'canceled' ? '当前计划已取消。'
              : validReport ? report
              : graph.status === 'completed' ? '实验执行完成，可点击右侧节点查看真实输出、报告和证据。'
              : failure?.error || '执行失败，运行记录已保留，可查看失败节点后继续处理。';
            return { ...session, planGraph: graph, reportedPlanId: graph.id, reportedPlanStatus: graph.status,
              chatHistory: [...session.chatHistory, { role: 'system', text }] };
          }) }));
        // 失败后仍有低频同步：同一计划重试成功时，必须把新的实际报告写回聊天。
        if (response.plan_graph.status !== 'completed')
          timer = setTimeout(refreshPlan,
            ['failed', 'canceled'].includes(response.plan_graph.status ?? '') ? 15000 : 5000);
      } catch {
        if (!canceled) timer = setTimeout(refreshPlan, 5000);
      }
    }
    void refreshPlan();
    return () => { canceled = true; if (timer) clearTimeout(timer); };
  }, [activeIdentity, activePlanId, activeSession?.id]);
  const sessionSummaries = useMemo<SessionSummary[]>(
    () =>
      [...persistedState.sessions]
        .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
        .map((session) => ({
          id: session.id,
          title: session.title,
          createdAt: session.createdAt,
          updatedAt: session.updatedAt,
          messageCount: session.chatHistory.length,
        })),
    [persistedState.sessions],
  );

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(persistedState));
  }, [persistedState]);

  useEffect(() => {
    onPlanGraphChanged(activeSession?.planGraph ?? null);
  }, [activeSession?.id, activeSession?.planGraph, onPlanGraphChanged]);

  const appendMessageToSession = useCallback((sessionId: string, message: ChatMessage) => {
    const now = new Date().toISOString();
    setPersistedState((prev) => ({
      ...prev,
      sessions: updateSessionRecord(prev.sessions, sessionId, (session) => {
        const nextChatHistory = [...session.chatHistory, message];
        return {
          ...session,
          chatHistory: nextChatHistory,
          updatedAt: now,
          title: deriveSessionTitle(session.title, nextChatHistory),
        };
      }),
    }));
  }, []);

  const appendChatMessage = useCallback(
    (message: ChatMessage) => {
      if (!persistedState.activeSessionId) return;
      appendMessageToSession(persistedState.activeSessionId, message);
    },
    [appendMessageToSession, persistedState.activeSessionId],
  );

  const handleLogin = useCallback(() => {
    const nextUserId = sanitizeUserId(loginInput);
    if (!nextUserId) return;

    setPersistedState((prev) => {
      if (prev.userId === nextUserId && prev.sessions.length > 0 && prev.activeSessionId) {
        return prev;
      }
      // 服务端资源属于原身份；切换身份建立新会话，不迁移其计划引用。
      return {
        ...createSessionState(nextUserId, '新会话'),
      };
    });
    setLoginInput(nextUserId);
    setPrompt('');
	setPendingAttachments([]);
	setAttachmentError('');
  }, [loginInput]);

  const handleCreateSession = useCallback(() => {
    const sessionTitlePrefix = persistedState.userId ? '新会话' : '临时会话';
    const nextSession = {
      ...createSessionRecord(persistedState.sessions.length + 1),
      title: `${sessionTitlePrefix} ${persistedState.sessions.length + 1}`,
    };
    setPersistedState((prev) => ({
      ...prev,
      activeSessionId: nextSession.id,
      sessions: [nextSession, ...prev.sessions],
    }));
    setPrompt('');
	setPendingAttachments([]);
	setAttachmentError('');
  }, [persistedState.sessions.length, persistedState.userId]);

  const handleSwitchSession = useCallback((sessionId: string) => {
    setPersistedState((prev) => ({
      ...prev,
      activeSessionId: sessionId,
    }));
    setPrompt('');
	setPendingAttachments([]);
	setAttachmentError('');
  }, []);

	const handleAttachFiles = useCallback(async (files: File[]) => {
		if (files.length === 0 || !persistedState.activeSessionId) return;
		const availableSlots = Math.max(0, 8 - pendingAttachments.length);
		const selected = files.slice(0, availableSlots);
		if (selected.length === 0) {
			setAttachmentError('每个计划最多添加 8 个附件');
			return;
		}
		setUploadingAttachments(true);
		setAttachmentError('');
		const identity = {
			userId: persistedState.userId ?? guestUserId,
			sessionId: persistedState.activeSessionId,
		};
		const uploaded: UploadedFile[] = [];
		try {
			for (const file of selected) {
				uploaded.push(await uploadFile(file, identity));
			}
			setPendingAttachments((current) => [...current, ...uploaded].slice(0, 8));
		} catch (error) {
			console.error(error);
			setAttachmentError('附件上传失败，请检查文件类型和大小');
		} finally {
			setUploadingAttachments(false);
		}
	}, [guestUserId, pendingAttachments.length, persistedState.activeSessionId, persistedState.userId]);

	const handleRemoveAttachment = useCallback((uploadId: string) => {
		setPendingAttachments((current) => current.filter((attachment) => attachment.id !== uploadId));
		setAttachmentError('');
	}, []);

  const updateSessionPlanState = useCallback(
    (sessionId: string, payload: { activePlanId: string | null; intentContext: IntentContext | null; planGraph: PlanGraph | null }) => {
      const now = new Date().toISOString();
      setPersistedState((prev) => ({
        ...prev,
        sessions: updateSessionRecord(prev.sessions, sessionId, (session) => ({
          ...session,
          activePlanId: payload.activePlanId,
          intentContext: payload.intentContext,
          planGraph: payload.planGraph,
          updatedAt: now,
        })),
      }));
    },
    [],
  );

  const handleSendMessage = useCallback(async () => {
	if ((!prompt.trim() && pendingAttachments.length === 0) || !persistedState.activeSessionId || uploadingAttachments) return;

	const userPrompt = prompt.trim() || '请分析上传的材料，并设计预算受限的轻量消融实验。';
	const attachmentNames = pendingAttachments.map((attachment) => attachment.name);
	const attachmentIDs = pendingAttachments.map((attachment) => attachment.id);
    const requestUserId = persistedState.userId ?? guestUserId;
    const requestSessionId = persistedState.activeSessionId;
    setLoading(true);
	appendMessageToSession(requestSessionId, {
		role: 'user',
		text: attachmentNames.length > 0 ? `${userPrompt}\n\n附件：${attachmentNames.join('、')}` : userPrompt,
	});
    setPrompt('');

    try {
      const response = await sendMessage(userPrompt, {
        userId: requestUserId, sessionId: requestSessionId,
      }, attachmentIDs);
      if (response.type === 'plan') {
        const graph = response.plan_graph;
        if (!graph) throw new Error('Backend did not return plan_graph');
        updateSessionPlanState(requestSessionId, {
          intentContext: response.intent_context ?? null,
          activePlanId: graph.id, planGraph: graph,
        });
      }
      appendMessageToSession(requestSessionId, {
        role: 'system', text: response.response,
        actions: response.papers?.length ? ['open_pdf'] : undefined,
        papers: response.papers,
      });
      // 后端已持久化追问所需附件，简短回复不必重新上传或再次选择材料。
      setPendingAttachments([]);
      setAttachmentError('');
    } catch (error) {
      console.error(error);
      appendMessageToSession(requestSessionId, {
        role: 'system', text: apiErrorMessage(error, uiText.backendError),
      });
    } finally {
      setLoading(false);
    }
	}, [appendMessageToSession, guestUserId, pendingAttachments, persistedState.activeSessionId, persistedState.userId, prompt, updateSessionPlanState, uploadingAttachments]);

  return {
    acceptResearchPlan: (response: PlanResponse) => {
      if (!persistedState.activeSessionId) return;
      updateSessionPlanState(persistedState.activeSessionId, {
        activePlanId: response.plan_graph.id, intentContext: response.intent_context ?? null,
        planGraph: response.plan_graph,
      });
      setPendingAttachments([]);
      appendChatMessage({ role: 'system', text: '实验计划已生成，可在右侧执行并查看证据。',
        actions: response.papers?.length ? ['open_pdf'] : undefined, papers: response.papers });
    },
    prompt,
    setPrompt,
    loading,
	pendingAttachments,
	uploadingAttachments,
	attachmentError,
    chatHistory,
    activePlanId,
	activePlanStatus,
    intentContext,
    appendChatMessage,
    isLoggedIn: Boolean(persistedState.userId),
    userId: persistedState.userId,
	requestIdentity: {
		userId: persistedState.userId ?? guestUserId,
		sessionId: persistedState.activeSessionId ?? 'session-unavailable',
	},
    loginInput,
    setLoginInput,
    activeSessionId: persistedState.activeSessionId,
    sessionSummaries,
    handleLogin,
    handleCreateSession,
    handleSwitchSession,
    handleSendMessage,
	handleAttachFiles,
	handleRemoveAttachment,
  };
}
