import { useCallback, useRef, useState } from 'react';
import type { DocumentLoadEvent, PdfJs } from '@react-pdf-viewer/core';
import { defaultLayoutPlugin } from '@react-pdf-viewer/default-layout';
import { useAITranslationPlugin } from '../../hooks/useAITranslationPlugin';
import type { ChatMessage } from '../../contracts/api';
import { chat } from '../../services/api/scholarApi';
import type { RequestIdentity } from '../../services/api/scholarApi';
import { readDocumentText } from '../../features/pdf-viewer/readDocumentText';

interface UsePdfAssistFlowOptions {
  setPrompt: (value: string) => void;
  appendChatMessage: (message: ChatMessage) => void;
  appendSelectedTaskLog: (line: string) => void;
  identity: RequestIdentity;
}

export function usePdfAssistFlow(options: UsePdfAssistFlowOptions) {
  const { setPrompt, appendChatMessage, appendSelectedTaskLog, identity } = options;
  const documentRef = useRef<PdfJs.PdfDocument | null>(null);
  const [pdfUrl, setCurrentPdfUrl] = useState<string | null>(null);
  const setPdfUrl = useCallback((url: string | null) => {
    documentRef.current = null;
    setCurrentPdfUrl(url);
  }, []);
  const [isFullTranslating, setIsFullTranslating] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(true);
  const defaultLayoutPluginInstance = defaultLayoutPlugin();

  const handleAskAI = useCallback(
    (selectedText: string) => {
      setPrompt(`请帮我详细解释这篇文献中的这段内容：\n"${selectedText}"`);
      setPdfUrl(null);
      appendSelectedTaskLog('[System] 已获取划词内容，准备向 ScholarAgent 发起追问...');
    },
    [appendSelectedTaskLog, setPrompt, setPdfUrl],
  );

  const translate = async (text: string) => {
    if (text.length > 12000) throw new Error('所选内容超过翻译字数上限。');
    return (await chat(`将以下文献内容翻译为中文，保留原文的限定与公式：\n${text}`, identity)).response;
  };
  const aiTranslationPluginInstance = useAITranslationPlugin(translate, handleAskAI);

  const handleFullTranslation = useCallback(async () => {
    if (isFullTranslating) return;
    setIsFullTranslating(true);
    try {
      if (!documentRef.current) throw new Error('文档尚未加载。');
      const text = await readDocumentText(documentRef.current);
      const response = await chat(`逐页翻译以下 PDF 内容为中文，保留缺失文字状态，不补造内容：\n${text}`, identity);
      appendChatMessage({ role: 'assistant', text: response.response });
      setPdfUrl(null);
    } catch (failure) {
      appendChatMessage({ role: 'system', text: failure instanceof Error && !('response' in failure)
        ? failure.message : '全文翻译请求失败，请检查模型配置与 API 连接。' });
      setPdfUrl(null);
    } finally {
      setIsFullTranslating(false);
    }
  }, [appendChatMessage, identity, isFullTranslating, setPdfUrl]);

  return {
    pdfUrl,
    setPdfUrl,
    isFullTranslating,
    showSuggestions,
    setShowSuggestions,
    defaultLayoutPluginInstance,
    aiTranslationPluginInstance,
    handleFullTranslation,
    onDocumentLoad: (event: DocumentLoadEvent) => { documentRef.current = event.doc; },
  };
}
