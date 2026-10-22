import { expect, test, type Page } from '@playwright/test';
import { pdfFixture } from './pdfFixture';

const planId = '3344556677889900aabbccddeeff0011';
const taskId = '44556677889900aabbccddeeff001122';
const code = 'assert sum([2, 3]) == 5\nprint("LAYOUT_TEST_OK")\n';
const report = '# 界面回归报告\n\n此内容来自界面显示夹具，用于验证报告阅读与下载。\n\n```text\nLAYOUT_TEST_OK\n```';
const image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==';

async function prepare(page: Page, status = 'completed', evidence = false, summary = false) {
  const graph = {
    id: planId, user_intent: '科研工作台界面验收', intent_type: 'Code_Execution', status,
    trace_id: 'layout-fixture', approval: { required: status === 'awaiting_approval', status: 'pending' },
    budget: { max_task_attempts: 5, max_duration_seconds: 120 }, usage: { task_attempts: 1 }, edges: [],
    nodes: [{ id: taskId, name: '界面验收节点', assigned_to: 'coder_agent', type: evidence ? 'research_execute' : 'code_execute',
      status: status === 'awaiting_approval' ? 'pending' : status, description: '显示行为夹具', dependencies: [],
      required_artifacts: [], output_artifacts: [], inputs: {}, run_count: 1, code, result: report,
      image_base64: image, contract: { version: 'task/v1', input_artifacts: [], output_artifacts: [], allowed_tools: [] } }],
  };
  if (summary) Object.assign(graph, { nodes: [graph.nodes[0], { ...graph.nodes[0],
    id: '556677889900aabbccddeeff00112233', name: '汇总报告节点', assigned_to: 'chat_agent', type: 'chat_process',
    dependencies: [taskId], code: '', image_base64: '', result: '# 汇总报告\n\n报告选中态验收。',
  }], edges: [{ id: 'summary-edge', from: taskId, to: '556677889900aabbccddeeff00112233', type: 'control' }] });
  if (evidence) Object.assign(graph.nodes[0], { structured_data: JSON.stringify({ version: 'claim.evidence/v1', status: 'completed', rubric_sha256: 'ui-fixture',
    claims: [{ claim_id: 'claim-ui', title: '界面主张夹具', statement: '验证证据关系的展示', source_locator: 'UI fixture', claim_type: 'test',
      status: 'verified', confidence: 1, criteria: [{ criterion_id: 'criterion-ui', description: '证据引用可见', status: 'verified', confidence: 1,
        evidence_ids: ['evidence-ui'], reason: '界面显示夹具，不是科学结论' }] }],
    evidence: [{ id: 'evidence-ui', artifact_key: 'ui-fixture', evidence_type: 'test_report', available: true, summary: '界面证据夹具' }],
    summary: { total_claims: 1, total_criteria: 1, verified: 1, partially_reproduced: 0, contradicted: 0, unverifiable: 0,
      blocked_by_missing_asset: 0, criterion_evidence_coverage: 1 } }) });
  await page.addInitScript(graph => {
    if (localStorage.getItem('openresearch-python.chat-sessions.v1')) return;
    localStorage.setItem('openresearch-python.chat-sessions.v1', JSON.stringify({ userId: null,
      activeSessionId: 'layout-session', sessions: [{ id: 'layout-session', title: '工作台验收会话',
        createdAt: '2026-10-22', updatedAt: '2026-10-22', activePlanId: graph.id, planGraph: graph, intentContext: null,
        chatHistory: [{ role: 'user', text: '验证工作台布局与报告阅读' }, { role: 'assistant', text: '请在流程节点查看界面回归报告。' }] },
        { id: 'other-session', title: '文献对比研究', createdAt: '2026-10-22', updatedAt: '2026-10-22',
          activePlanId: null, planGraph: null, intentContext: null, chatHistory: [] }] }));
  }, graph);
  await page.route(`**/api/plans/${planId}`, route => route.fulfill({ json: { plan_graph: graph } }));
  await page.route(`**/api/plans/${planId}/stream*`, route => route.fulfill({ contentType: 'text/event-stream',
    body: `id: 1\nevent: plan_event\ndata: ${JSON.stringify({ sequence: 1, plan_id: planId, task_id: taskId,
      event_type: 'task_log', timestamp: '2026-10-22T00:00:00Z', payload: { message: 'LAYOUT_HISTORY_LOG' } })}\n\n` }));
  await page.goto('/');
}

test('桌面三栏互不覆盖，报告/代码/日志/图表与下载和全屏保留', async ({ page }, testInfo) => {
  await prepare(page);
  await page.locator('.react-flow__node').filter({ hasText: '界面验收节点' }).click();
  const inspector = page.getByRole('complementary', { name: '任务详情' });
  for (const width of [1600, 1280]) {
    await page.setViewportSize({ width, height: 1000 });
    const nav = await page.getByRole('complementary', { name: '会话导航' }).boundingBox();
    const main = await page.getByRole('main', { name: '研究工作区' }).boundingBox();
    const panel = await inspector.boundingBox();
    expect(nav && main && panel).toBeTruthy();
    expect(nav!.x + nav!.width).toBeLessThanOrEqual(main!.x + 1);
    expect(main!.x + main!.width).toBeLessThanOrEqual(panel!.x + 1);
    expect(panel!.x + panel!.width).toBeLessThanOrEqual(width + 1);
    await expect(page.getByRole('textbox', { name: '研究问题' })).toBeInViewport();
  }
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.getByRole('button', { name: '分析报告', exact: true }).click();
  await expect(inspector.getByRole('heading', { name: '界面回归报告' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('desktop-workbench.png'), fullPage: true });
  await page.getByTitle('全屏阅读报告').click();
  await expect(page.locator('.scholar-execution-sidebar.is-expanded')).toBeVisible();
  await page.getByRole('button', { name: '退出全屏查看' }).click();
  await page.getByRole('button', { name: '沙箱代码', exact: true }).click();
  await expect(inspector.locator('pre')).toContainText(code.trim());
  await page.getByRole('button', { name: '实时日志', exact: true }).click();
  await expect(inspector.getByRole('log')).toContainText('LAYOUT_HISTORY_LOG');
  await page.getByRole('button', { name: '生成图表', exact: true }).click();
  await expect(page.getByAltText('Generated Plot')).toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: /experiment.py/ }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('experiment.py');
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString()).toBe(code);
  await page.getByRole('button', { name: '关闭节点执行面板' }).click();
  await page.getByRole('button', { name: '展开执行流程' }).click();
  await expect(page.locator('.workspace-flow.is-expanded')).toBeVisible();
  await page.getByRole('button', { name: '收起执行流程' }).click();
});

test('会话搜索、切换和身份设置继续可用', async ({ page }) => {
  await prepare(page);
  await page.getByRole('textbox', { name: '搜索会话' }).fill('文献');
  await expect(page.getByRole('region', { name: '历史会话' }).getByRole('button')).toHaveCount(1);
  await page.getByRole('button', { name: /文献对比研究/ }).click();
  await expect(page.getByRole('heading', { name: '文献对比研究', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /本地工作空间/ }).click();
  await page.getByRole('textbox', { name: '用户 ID' }).fill('workbench-test-user');
  await page.getByRole('button', { name: '设置身份', exact: true }).click();
  await expect(page.getByRole('complementary', { name: '会话导航' })).toContainText('workbench-test-user');
  await page.getByRole('button', { name: '新建 Session', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '搜索会话' })).toHaveValue('');
});

test('从概览打开汇总报告时同步高亮流程节点，关闭详情清除高亮', async ({ page }) => {
  await prepare(page, 'completed', false, true);
  await page.locator('.scholar-task-node').filter({ hasText: '界面验收节点' }).click();
  await page.getByRole('button', { name: '查看报告', exact: true }).click();
  await expect(page.getByRole('complementary', { name: '任务详情' }).getByRole('heading', { name: '汇总报告节点' })).toBeVisible();
  await expect(page.locator('.scholar-task-node.selected')).toHaveCount(1);
  await expect(page.locator('.scholar-task-node.selected')).toContainText('汇总报告节点');
  await page.getByRole('button', { name: '关闭节点执行面板' }).click();
  await expect(page.locator('.scholar-task-node.selected')).toHaveCount(0);
});

test('手机导航抽屉、流程和报告可达，输入区与设置没有水平溢出', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepare(page);
  await page.getByRole('button', { name: '打开会话导航' }).click();
  await expect(page.getByRole('region', { name: '历史会话' })).toBeVisible();
  await page.getByRole('button', { name: /工作台验收会话/ }).click();
  await expect(page.getByRole('complementary', { name: '会话导航' })).not.toBeVisible();
  await page.getByRole('button', { name: '对话', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '研究问题' })).toBeInViewport();
  await page.getByRole('button', { name: '高级实验设置', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '配置复现实验' })).toBeVisible();
  await page.getByRole('button', { name: '关闭实验配置' }).click();
  await page.getByRole('button', { name: /^流程/ }).click();
  await page.locator('.react-flow__node').filter({ hasText: '界面验收节点' }).click();
  await page.getByRole('button', { name: '分析报告', exact: true }).click();
  await expect(page.getByRole('heading', { name: '界面回归报告' })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath('mobile-workbench.png'), fullPage: true });
});

test('PDF 实际阅读、全文翻译与切换会话后的阅读上下文隔离', async ({ page }) => {
  await prepare(page);
  await page.locator('input[type="file"]').setInputFiles({ name: 'workspace-reader.pdf', mimeType: 'application/pdf', buffer: pdfFixture() });
  await page.getByRole('button', { name: '阅读 workspace-reader.pdf', exact: true }).click();
  await expect(page.getByText('PDF reader verification', { exact: true })).toBeVisible();
  let requestedText = '';
  await page.route('**/api/chat', route => {
    requestedText = route.request().postDataJSON().message;
    return route.fulfill({ json: { response: '全文翻译界面回归结果' } });
  });
  await page.getByRole('button', { name: '全文翻译', exact: true }).click();
  await expect(page.getByText('全文翻译界面回归结果', { exact: true })).toBeVisible();
  expect(requestedText).toContain('PDF reader verification');
  await page.getByRole('button', { name: '阅读 workspace-reader.pdf', exact: true }).click();
  await expect(page.getByText('PDF reader verification', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /文献对比研究/ }).click();
  await expect(page.getByRole('region', { name: '论文阅读器' })).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '研究问题' })).toBeInViewport();
});

test('主张证据图保留节点关系与完整展开视图', async ({ page }) => {
  await prepare(page, 'completed', true);
  await page.locator('.react-flow__node').filter({ hasText: '界面验收节点' }).click();
  const panel = page.getByRole('complementary', { name: '任务详情' });
  await page.getByRole('button', { name: '证据图', exact: true }).click();
  await expect(panel.getByText('1 个主张 · 1 个准则', { exact: true })).toBeVisible();
  await expect(panel.getByText('界面主张夹具', { exact: true })).toBeVisible();
  await page.getByTitle('全屏查看证据图').click();
  await expect(page.locator('.scholar-execution-sidebar.is-expanded')).toBeVisible();
  await panel.locator('.react-flow__node').filter({ hasText: 'ui-fixture' }).click();
  await expect(panel.getByText('界面证据夹具', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '退出全屏查看' }).click();
});

for (const [status, button, endpoint] of [
  ['awaiting_approval', '审批并运行', 'approve'], ['in_progress', '取消', 'cancel'], ['failed', '重试失败节点', `tasks/${taskId}/retry`],
]) {
  test(`${button}仍调用原计划接口`, async ({ page }) => {
    await prepare(page, status);
    await page.route(`**/api/plans/${planId}/${endpoint}`, route => route.fulfill({ json: { status: 'accepted' } }));
    await page.route(`**/api/plans/${planId}/execute`, route => route.fulfill({ json: { status: 'accepted' } }));
    const request = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith(`/${endpoint}`));
    await page.getByRole('button', { name: button, exact: true }).click();
    const actual = await request;
    expect(actual.headers()['x-session-id']).toBe('layout-session');
  });
}
