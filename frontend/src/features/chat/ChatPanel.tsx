import type { ChatMessage, UploadedFile } from '../../contracts/api';
import { ChatComposer } from './ChatComposer';
import { ChatMessageList } from './ChatMessageList';

interface ChatPanelState {
  chatHistory: ChatMessage[];
  loading: boolean;
  prompt: string;
  showSuggestions: boolean;
	pendingAttachments: UploadedFile[];
	uploadingAttachments: boolean;
	attachmentError: string;
  isLoggedIn: boolean;
  userId: string | null;
  loginInput: string;
  activeSessionId: string | null;
  sessions: Array<{
    id: string;
    title: string;
    createdAt: string;
    updatedAt: string;
    messageCount: number;
  }>;
}

interface ChatActions {
  setPrompt: (value: string) => void;
  setShowSuggestions: (next: boolean) => void;
  onSendMessage: () => void;
  setLoginInput: (value: string) => void;
  onLogin: () => void;
  onCreateSession: () => void;
  onSwitchSession: (sessionId: string) => void;
	onAttachFiles: (files: File[]) => void;
	onRemoveAttachment: (uploadId: string) => void;
}

interface PdfActions {
  onReadPaper: (attachment: UploadedFile) => void;
  onClosePdf: () => void;
  onFullTranslation: () => void;
}

interface TaskActions {
  onOpenTaskView: (taskId: string, mode: 'plot' | 'report') => void;
}

interface ChatPanelProps {
  state: ChatPanelState;
  chatActions: ChatActions;
  pdfActions: PdfActions;
  taskActions: TaskActions;
}

export function ChatPanel(props: ChatPanelProps) {
  const { state, chatActions, pdfActions, taskActions } = props;

  return (
    <div className="chat-panel">
      <ChatMessageList
        chatHistory={state.chatHistory}
        loading={state.loading}
        isLoggedIn={state.isLoggedIn}
        pdfActions={pdfActions}
        taskActions={taskActions}
      />
      <ChatComposer
        onReadPaper={pdfActions.onReadPaper}
        prompt={state.prompt}
        loading={state.loading}
        isLoggedIn={state.isLoggedIn}
        showSuggestions={state.showSuggestions}
		pendingAttachments={state.pendingAttachments}
		uploadingAttachments={state.uploadingAttachments}
		attachmentError={state.attachmentError}
        setPrompt={chatActions.setPrompt}
        setShowSuggestions={chatActions.setShowSuggestions}
        onSendMessage={chatActions.onSendMessage}
		onAttachFiles={chatActions.onAttachFiles}
		onRemoveAttachment={chatActions.onRemoveAttachment}
      />
    </div>
  );
}
