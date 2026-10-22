import { useCallback, useEffect, useRef } from 'react';
import { isAxiosError } from 'axios';
import type { PlanEvent } from '../../contracts/api';
import { isPlanTerminalEvent } from '../../contracts/events';
import { createPlanEventSource, getPlan, type RequestIdentity } from '../../services/api/scholarApi';

interface Options {
  planId: string | null;
  identity: RequestIdentity;
  onEvent: (event: PlanEvent) => void;
  onReset: () => void;
}

export function usePlanLogSubscription({ planId, identity, onEvent, onReset }: Options) {
  const sourceRef = useRef<EventSource | null>(null);
  const cursorRef = useRef(0);
  const contextRef = useRef<string | null>(null);
  const { userId, sessionId } = identity;
  const context = JSON.stringify([userId, sessionId, planId]);

  const subscribe = useCallback((identifier: string) => {
    if (contextRef.current !== context || identifier !== planId) return;
    sourceRef.current?.close();
    let ended = false;
    const source = createPlanEventSource(identifier, {
      onPlanEvent: event => {
        if (sourceRef.current !== source || event.plan_id !== identifier) return;
        // 持久序号覆盖历史回放、原生重连和显式重试，边界帧只能应用一次。
        if (event.sequence && event.sequence <= cursorRef.current) return;
        if (event.sequence) cursorRef.current = event.sequence;
        ended = isPlanTerminalEvent(event);
        onEvent(event);
      },
      onError: () => {
        // 历史中可能先失败再重试；等流结束后再关闭，不能截断旧终态之后的记录。
        if (ended && sourceRef.current === source) source.close();
      },
    }, cursorRef.current);
    sourceRef.current = source;
  }, [context, onEvent, planId]);

  useEffect(() => {
    contextRef.current = context;
    cursorRef.current = 0;
    onReset();
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function restore() {
      if (!planId) return;
      try {
        // 先按当前身份读取计划并绑定 Cookie，EventSource 随后回放全部历史并继续订阅。
        await getPlan(planId, { userId, sessionId });
        if (!disposed) subscribe(planId);
      } catch (error) {
        if (disposed || (isAxiosError(error) && error.response && error.response.status < 500)) return;
        timer = setTimeout(() => void restore(), 3000);
      }
    }
    void restore();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      contextRef.current = null;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, [context, onReset, planId, sessionId, subscribe, userId]);

  return subscribe;
}
