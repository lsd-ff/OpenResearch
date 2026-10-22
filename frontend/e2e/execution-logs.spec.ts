import { expect, test, type Page } from '@playwright/test';

const planId = '1234567890abcdef1234567890abcdef';
const taskId = '2234567890abcdef1234567890abcdef';
const report = '# 日志验收报告\n\n这是持久保存的完整节点报告。';
const sessionId = 'session-log-recovery';

function graph(status = 'completed') {
  return {
    id: planId, user_intent: '日志恢复验收', intent_type: 'Code_Execution', status,
    trace_id: 'logs-test', approval: { required: false, status: 'approved' },
    budget: { max_task_attempts: 2, max_duration_seconds: 120 }, usage: { task_attempts: 1 },
    edges: [], nodes: [{
      id: taskId, name: '日志验收节点', type: 'code_execute', description: '日志显示夹具',
      assigned_to: 'coder_agent', status, dependencies: [], inputs: {}, required_artifacts: [],
      output_artifacts: [], result: status === 'completed' ? report : '', run_count: 1,
      contract: { version: 'task/v1', input_artifacts: [], output_artifacts: [], allowed_tools: [] },
    }],
  };
}

function frame(sequence: number, type: string, payload: object = {}) {
  return `id: ${sequence}\nevent: plan_event\ndata: ${JSON.stringify({
    sequence, plan_id: planId, task_id: type.startsWith('plan_') ? undefined : taskId,
    event_type: type, task_status: type === 'task_completed' ? 'completed' : undefined,
    timestamp: '2026-10-22T00:00:00Z', payload,
  })}\n\n`;
}

async function savedSession(page: Page, status = 'completed') {
  await page.addInitScript(({ plan, sessionId }) => {
    if (localStorage.getItem('openresearch-python.chat-sessions.v1')) return;
    localStorage.setItem('openresearch-python.chat-sessions.v1', JSON.stringify({
      userId: 'logs-owner', activeSessionId: sessionId, sessions: [{
        id: sessionId, title: '日志恢复验收', createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(), chatHistory: [], activePlanId: plan.id,
        planGraph: plan, intentContext: null,
      }],
    }));
  }, { plan: graph(status), sessionId });
}

async function logs(page: Page) {
  await page.locator('.react-flow__node').filter({ hasText: '日志验收节点' }).click();
  await page.getByRole('button', { name: '实时日志', exact: true }).click();
  return page.locator('.scholar-execution-sidebar');
}

test('历史日志超过一页且包含旧失败，刷新和重开会话仍完整恢复报告与日志', async ({ page }) => {
  await savedSession(page);
  let starts = 0;
  page.on('request', request => { if (request.method() === 'POST') starts++; });
  await page.route(`**/api/plans/${planId}`, route => route.fulfill({ json: { plan_graph: graph() } }));
  const history = [frame(1, 'plan_failed', { error: '历史失败，后来已重试' }),
    ...Array.from({ length: 205 }, (_, index) => frame(index + 2, 'task_log', { message: `HISTORY_LINE_${index}` })),
    frame(207, 'task_completed', { result_summary: '事件中的简短摘要' }), frame(208, 'plan_completed')].join('');
  await page.route(`**/api/plans/${planId}/stream*`, route => route.fulfill({
    contentType: 'text/event-stream', body: history,
  }));
  await page.goto('/');
  const panel = await logs(page);
  await expect(panel.getByText(/HISTORY_LINE_204/)).toBeVisible();
  expect(await panel.innerText()).toContain('HISTORY_LINE_0');
  await page.getByRole('button', { name: '分析报告', exact: true }).click();
  await expect(panel.getByText('这是持久保存的完整节点报告。', { exact: true })).toBeVisible();
  await page.reload();
  await expect((await logs(page)).getByText(/HISTORY_LINE_204/)).toBeVisible();
  await page.getByRole('button', { name: /^新建.*Session$/ }).click();
  await page.getByRole('button', { name: /日志恢复验收/ }).click();
  await expect((await logs(page)).getByText(/HISTORY_LINE_204/)).toBeVisible();
  await page.getByRole('button', { name: '分析报告', exact: true }).click();
  await expect(panel.getByText('这是持久保存的完整节点报告。', { exact: true })).toBeVisible();
  expect(starts).toBe(0);
});

test('自动执行订阅实时事件，断流重连不重复日志，完成后仍保留完整报告', async ({ page }) => {
  await savedSession(page, 'in_progress');
  let finished = false;
  let connections = 0;
  let finish!: () => void;
  const release = new Promise<void>(resolve => { finish = resolve; });
  await page.route(`**/api/plans/${planId}`, route => route.fulfill({
    json: { plan_graph: graph(finished ? 'completed' : 'in_progress') },
  }));
  await page.route(`**/api/plans/${planId}/stream*`, async route => {
    connections++;
    if (connections === 1) {
      await route.fulfill({ contentType: 'text/event-stream', body: 'retry: 100\n\n' +
        frame(1, 'task_started') + frame(2, 'task_log', { message: 'LIVE_FIRST_MARKER' }) });
      return;
    }
    await release;
    finished = true;
    await route.fulfill({ contentType: 'text/event-stream', body:
      frame(2, 'task_log', { message: 'LIVE_FIRST_MARKER' }) +
      frame(3, 'task_log', { message: 'LIVE_SECOND_MARKER' }) +
      frame(4, 'task_completed', { result: report }) + frame(5, 'plan_completed') });
  });
  await page.goto('/');
  await page.locator('.react-flow__node').filter({ hasText: '日志验收节点' }).click();
  const panel = page.locator('.scholar-execution-sidebar');
  await expect(panel.getByText(/LIVE_FIRST_MARKER/)).toBeVisible();
  await expect(page.getByRole('button', { name: '实时日志', exact: true })).toBeVisible();
  finish();
  await expect(page.getByRole('button', { name: '分析报告', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '实时日志', exact: true }).click();
  await expect(panel.getByText(/LIVE_SECOND_MARKER/)).toBeVisible();
  expect((await panel.innerText()).match(/LIVE_FIRST_MARKER/g)).toHaveLength(1);
  await page.getByRole('button', { name: '分析报告', exact: true }).click();
  await expect(panel.getByText('这是持久保存的完整节点报告。', { exact: true })).toBeVisible();
});
