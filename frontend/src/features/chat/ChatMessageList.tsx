import { Bot, FileText, Languages, Maximize2, Network, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatMessage, UploadedFile } from '../../contracts/api';

interface PdfActions {
  onReadPaper: (attachment: UploadedFile) => void;
  onClosePdf: () => void;
  onFullTranslation: () => void;
}

interface TaskActions {
  onOpenTaskView: (taskId: string, mode: 'plot' | 'report') => void;
}

interface ChatMessageListProps {
  chatHistory: ChatMessage[];
  loading: boolean;
  isLoggedIn: boolean;
  pdfActions: PdfActions;
  taskActions: TaskActions;
}

export function ChatMessageList(props: ChatMessageListProps) {
  const { chatHistory, loading, isLoggedIn, pdfActions, taskActions } = props;

  return (
    <div className="chat-thread" role="log" aria-label="研究对话">
      {!isLoggedIn && (
        <div className="guest-session-note">
          当前浏览器保留游客会话；清除浏览器数据会丢失本地会话列表。实验状态由服务端保存。
        </div>
      )}
      {chatHistory.map((msg, i) => (
        <div key={i} className={`chat-message ${msg.role === 'user' ? 'is-user' : 'is-assistant'}`}>
          {msg.role !== 'user' && <div className="assistant-avatar" aria-hidden="true"><Network size={18} /></div>}
          <div className="chat-message-body">
            {msg.role !== 'user' && <div className="assistant-label">OpenResearch</div>}
            <div
              className="chat-message-bubble"
            >
              {msg.role === 'user' ? (
                msg.text
              ) : (
                <div className="prose prose-sm prose-slate max-w-none prose-p:leading-snug prose-pre:my-1 prose-pre:bg-gray-800 prose-pre:text-gray-100 prose-code:text-blue-600 prose-code:bg-blue-50 prose-code:px-1 prose-code:rounded">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
                </div>
              )}
            </div>

            {msg.actions && (msg.actions.some(action => action !== 'open_pdf') || Boolean(msg.papers?.length)) && (
              <div className="chat-message-actions">
                {msg.actions.includes('open_pdf') && msg.papers?.map(paper => (
                  <button
                    key={paper.id}
                    onClick={() => pdfActions.onReadPaper(paper)}
                    className="flex items-center gap-1 text-xs bg-white text-blue-600 border border-blue-200 px-3 py-1.5 rounded-full hover:bg-blue-50 shadow-sm transition-all active:scale-95"
                  >
                    <FileText className="w-3 h-3" />
                    阅读 {paper.name}
                  </button>
                ))}
                {msg.actions.includes('translate_full') && (
                  <button
                    onClick={pdfActions.onFullTranslation}
                    className="flex items-center gap-1 text-xs bg-white text-purple-600 border border-purple-200 px-3 py-1.5 rounded-full hover:bg-purple-50 shadow-sm transition-all active:scale-95"
                  >
                    <Languages className="w-3 h-3" />
                    全文翻译
                  </button>
                )}
                {msg.actions.includes('close_pdf') && (
                  <button
                    onClick={pdfActions.onClosePdf}
                    className="flex items-center gap-1 text-xs bg-white text-gray-600 border border-gray-200 px-3 py-1.5 rounded-full hover:bg-gray-50 shadow-sm transition-all active:scale-95"
                  >
                    <X className="w-3 h-3" />
                    关闭阅读器
                  </button>
                )}
                {msg.actions.includes('view_plot') && msg.taskId && (
                  <button
                    onClick={() => taskActions.onOpenTaskView(msg.taskId as string, 'plot')}
                    className="flex items-center gap-1 text-xs bg-white text-orange-600 border border-orange-200 px-3 py-1.5 rounded-full hover:bg-orange-50 shadow-sm transition-all active:scale-95"
                  >
                    <Maximize2 className="w-3 h-3" />
                    查看生成的图表
                  </button>
                )}
                {msg.actions.includes('view_report') && msg.taskId && (
                  <button
                    onClick={() => taskActions.onOpenTaskView(msg.taskId as string, 'report')}
                    className="flex items-center gap-1 text-xs bg-white text-green-600 border border-green-200 px-3 py-1.5 rounded-full hover:bg-green-50 shadow-sm transition-all active:scale-95"
                  >
                    <FileText className="w-3 h-3" />
                    查看分析报告
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      ))}
      {loading && (
        <div className="flex justify-start">
          <div className="bg-gray-100 rounded-2xl rounded-bl-none px-4 py-3 text-gray-500 animate-pulse flex items-center gap-2">
            <Bot className="w-4 h-4" />
            正在处理请求...
          </div>
        </div>
      )}
    </div>
  );
}
