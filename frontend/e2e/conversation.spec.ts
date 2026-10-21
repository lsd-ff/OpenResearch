import { expect, test, type Page } from '@playwright/test';
import { pdfFixture } from './pdfFixture';

test('一条聊天请求自动比较结果、修复差距并返回同图实际报告', async ({ page }, testInfo) => {
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'native-gap.csv', mimeType: 'text/csv',
    buffer: Buffer.from('id,x,outcome\n' + Array.from({ length: 12 }, (_, i) =>
      `${i},${i % 2},${i % 2 ? 'yes' : 'no'}\n`).join('')),
  });
  await expect(page.getByRole('button', { name: '移除 native-gap.csv', exact: true })).toBeVisible();
  const code = "import json, os, pathlib\n" +
    "rows = json.loads(pathlib.Path(os.environ['OPENRESEARCH_INPUT']).read_text())\n" +
    "assert all('outcome' not in row['inputs'] for row in rows)\n" +
    "predictions = [{'id': row['id'], 'prediction': 'yes' if row['inputs']['x'] == '0' else 'no'} for row in rows]\n" +
    "pathlib.Path(os.environ['OPENRESEARCH_PREDICTIONS']).write_text(json.dumps(predictions))\n" +
    "print('ACTUAL_NATIVE_ROWS', len(rows))\n";
  const response = await send(page, 'UI_NATIVE_GAP_FLOW benchmark：输入列 x，标签列 outcome，ID 列 id。' +
    '先测量原入口，比较结果并修复差距后重跑，保留原断言和数据，准确率要求至少 0.9。\n```python\n' + code + '```');
  const received = await response.json();
  expect(received.response).toContain('已开始执行');
  const allHeaders = await response.request().allHeaders();
  const headers = { 'X-User-Id': allHeaders['x-user-id'], 'X-Session-Id': allHeaders['x-session-id'] };
  await expect(page.getByText('科研执行与结果差距报告', { exact: true })).toBeVisible({ timeout: 60_000 });
  const graph = (await (await page.request.get(`/api/plans/${received.plan_graph.id}`, { headers })).json()).plan_graph;
  expect(graph.status).toBe('completed');
  expect(graph.nodes.every((node: { run_count: number }) => node.run_count === 1)).toBe(true);
  const artifact = graph.artifacts.result_gap__gap_debug_report;
  const debug = JSON.parse(await (await page.request.get(`/api/plans/${graph.id}/artifacts/${artifact.id}/content`, { headers })).text());
  expect(debug.before_metrics.baseline.accuracy).toBe(0);
  expect(debug.after_metrics.baseline.accuracy).toBe(1);
  expect(debug.source.source_plan_id).toBe(graph.id);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByText('科研执行与结果差距报告', { exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByText('科研执行与结果差距报告', { exact: true })).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath('native-gap-complete.png'), fullPage: true });
  await page.reload();
  await expect(page.getByText('科研执行与结果差距报告', { exact: true })).toHaveCount(1);
  const after = (await (await page.request.get(`/api/plans/${graph.id}`, { headers })).json()).plan_graph;
  expect(after.nodes.map((node: { run_count: number }) => node.run_count))
    .toEqual(graph.nodes.map((node: { run_count: number }) => node.run_count));
});

for (const rejected of [false, true]) {
test(`结果修复${rejected ? '拒绝删除断言' : '没有有据补丁'}时显示真实报告，刷新不重复`, async ({ page }, testInfo) => {
  await page.goto('/');
  const initial = await send(page, '运行 smoke 冒烟测试');
  const receivedHeaders = await initial.request().allHeaders();
  const headers = { 'X-User-Id': receivedHeaders['x-user-id'], 'X-Session-Id': receivedHeaders['x-session-id'] };
  const readResult = async (response: Awaited<ReturnType<typeof page.request.post>>) => {
    expect(response.status()).toBe(200);
    const event = (await response.text()).split('\n\n').find(item => item.startsWith('event: result\n'));
    expect(event).toBeDefined();
    return JSON.parse(event!.split('data: ', 2)[1]);
  };
  const original = await readResult(await page.request.post('/api/execute', { headers, data: {
    task_type: 'paper_code_execute', assigned_to: 'research_coding_agent',
    task_description: '运行固定原程序并允许有界修复', inputs: {
      code: 'assert sum([2,3]) == 5\nprint(5)\n', research: { max_repairs: 1, rubric: { claims: [{
        title: '固定数值规则', statement: 'accuracy >= 0.9', criteria: [{ description: 'accuracy',
          metric_name: 'accuracy', expected_value: 0.9, operator: 'at_least' }],
      }] } },
    },
  } }));
  const response = await page.request.post('/api/execute', { headers, data: {
    task_type: 'fix_and_rerun', assigned_to: 'research_coding_agent',
    task_description: rejected ? 'UI_GAP_REJECTED_PATCH_FIXTURE' : 'UI_GAP_NO_PATCH_FIXTURE',
    inputs: { workflow_ref: original.workflow_ref },
  } });
  let identifier: string;
  if (rejected) {
    expect(await response.text()).toContain('event: error');
    identifier = response.headers()['x-openresearch-plan-id'];
    const actual = await (await page.request.get(`/api/plans/${identifier}`, { headers })).json();
    expect(actual.plan_graph.status).toBe('failed');
    expect(actual.plan_graph.nodes[0].result).toContain('实际错误：');
  } else {
    const actual = await readResult(response);
    expect(JSON.parse(actual.outputs.gap_debug_report).execution_performed).toBe(false);
    expect(actual.outputs.research_report).toBeUndefined();
    identifier = actual.plan_id;
  }
  await page.goto(`/?plan=${identifier}`);
  await expect(page.getByText('结果修复', { exact: true })).toBeVisible();
  await expect(page.getByText('本次没有执行预检或实验。', { exact: true })).toBeVisible();
  await expect(page.getByText('实验执行完成，可点击右侧节点查看真实输出、报告和证据。', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath(rejected ? 'gap-rejected.png' : 'gap-no-patch.png'), fullPage: true });
  await page.reload();
  await expect(page.getByText('本次没有执行预检或实验。', { exact: true })).toHaveCount(1);
});
}

test('通用只读回答显示实际报告，刷新后不误报实验完成', async ({ page }, testInfo) => {
  await page.goto('/');
  const initial = await send(page, '运行 smoke 冒烟测试');
  const requestHeaders = await initial.request().allHeaders();
  const headers = { 'X-User-Id': requestHeaders['x-user-id'], 'X-Session-Id': requestHeaders['x-session-id'] };
  const response = await page.request.post('/api/execute', { headers, data: {
    task_type: 'general_process', assigned_to: 'general_agent', task_name: '通用只读回答',
    task_description: '解释通用回答界面，不运行任何代码', inputs: {},
  } });
  expect(response.status()).toBe(200);
  const event = (await response.text()).split('\n\n').find(item => item.startsWith('event: result\n'));
  expect(event).toBeDefined();
  const actual = JSON.parse(event!.split('data: ', 2)[1]);
  const report = JSON.parse(actual.outputs.general_response);
  expect(report.sources).toEqual([]);
  expect(report.scope).toContain('未作外部来源核验');
  await page.goto(`/?plan=${actual.plan_id}`);
  await expect(page.getByText('通用回答', { exact: true })).toBeVisible();
  await expect(page.getByText('1/1 完成', { exact: true })).toBeVisible();
  await expect(page.getByText('这是通用回答展示夹具；没有执行代码、测试或实验。')).toBeVisible();
  await expect(page.getByText('实验执行完成，可点击右侧节点查看真实输出、报告和证据。', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('general-response.png'), fullPage: true });
  await page.reload();
  await expect(page.getByText('这是通用回答展示夹具；没有执行代码、测试或实验。')).toHaveCount(1);
});

test('按链接打开 API 创建的计划并执行，刷新后保留真实结果', async ({ page }) => {
  await page.goto('/');
  const initial = await send(page, '运行 smoke 冒烟测试');
  const headers = await initial.request().allHeaders();
  const created = await page.request.post('/api/plan', {
    headers: { 'X-User-Id': headers['x-user-id'], 'X-Session-Id': headers['x-session-id'] },
    data: { intent: '通过链接查看独立计划', workflow: 'code', code: 'print(17)' },
  });
  expect(created.status()).toBe(200);
  const planId = (await created.json()).plan_graph.id;
  await page.goto(`/?plan=${planId}`);
  await expect(page.getByText('通过链接查看独立计划', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '运行计划', exact: true }).click();
  await expect(page.getByText('2/2 完成', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('2/2 完成', { exact: true })).toBeVisible();
  await page.locator('.react-flow__node').filter({ hasText: 'Execute Python Experiment' }).click();
  await page.getByRole('button', { name: '分析报告', exact: true }).click();
  await expect(page.getByText('17', { exact: true }).last()).toBeVisible();
});

test('直接执行错误 API 程序，实际修复并在聊天返回两次运行记录', async ({ page }) => {
  await page.goto('/');
  const code = `import json\nvalue = json.decode('{"number":7}')\nassert value['number'] == 7\nprint(value['number'])`;
  const message = ['直接执行以下代码并修复 API 错误，保留断言', '```python', code, '```'].join('\n');
  const response = await send(page, message);
  const payload = await response.json();
  expect(['ready', 'in_progress', 'completed']).toContain(payload.plan_graph.status);
  await expect(page.getByText(/运行\/安装尝试 2 次，采用 1 次修复/)).toBeVisible({ timeout: 90_000 });
  const headers = await response.request().allHeaders();
  const saved = await page.request.get(`/api/plans/${payload.plan_graph.id}`, {
    headers: { 'X-User-Id': headers['x-user-id'], 'X-Session-Id': headers['x-session-id'] },
  });
  const graph = (await saved.json()).plan_graph;
  expect(graph.status).toBe('completed');
  const task = graph.nodes.find((node: { type: string }) => node.type === 'code_execute');
  const report = JSON.parse(task.structured_data);
  expect(report.attempts.map((item: { output: { exit_code: number } }) => item.output.exit_code)).toEqual([1, 0]);
  expect(new Set(report.attempts.map((item: { sandbox_id: string }) => item.sandbox_id)).size).toBe(2);
  expect(report.attempts[1].output.stdout.trim()).toBe('7');
  await page.reload();
  await expect(page.getByText(/运行\/安装尝试 2 次，采用 1 次修复/)).toHaveCount(1);
});

test('上传 PDF 后直接在附件旁阅读，无需打开高级实验设置', async ({ page }, testInfo) => {
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'reader.pdf', mimeType: 'application/pdf', buffer: pdfFixture(),
  });
  await page.getByRole('button', { name: '阅读 reader.pdf', exact: true }).click();
  await expect(page.getByText('论文阅读 · 当前文档', { exact: true })).toBeVisible();
  await expect(page.getByText('PDF reader verification', { exact: true })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('pdf-reader.png'), fullPage: true });
});

test('真实运行结果可独立复核并绘图，刷新后保留图表和审核报告', async ({ page }, testInfo) => {
  await page.goto('/');
  const initial = await send(page, '运行 smoke 冒烟测试');
  const requestHeaders = await initial.request().allHeaders();
  const headers = { 'X-User-Id': requestHeaders['x-user-id'], 'X-Session-Id': requestHeaders['x-session-id'] };
  const outcome = (text: string) => {
    const event = text.split('\n\n').find(item => item.startsWith('event: result\n'));
    expect(event, text).toBeDefined();
    return JSON.parse(event!.split('data: ', 2)[1]);
  };
  const executed = await page.request.post('/api/execute', { headers, timeout: 60_000, data: {
    task_type: 'execute_code', assigned_to: 'coder_agent',
    task_description: '执行原断言，不要自动修复', inputs: { code: 'assert sum([2,3,5]) == 10\nprint(10)\n' },
  } });
  const source = outcome(await executed.text());
  const before = await (await page.request.get(`/api/plans/${source.plan_id}`, { headers })).json();
  const reviewed = await page.request.post('/api/execute', { headers, data: {
    task_type: 'render_plot', task_name: '实际结果复核图', assigned_to: 'data_agent',
    task_description: '绘制已有真实执行统计，不执行新实验', inputs: { workflow_ref: source.workflow_ref },
  } });
  const result = outcome(await reviewed.text());
  expect(result.source_workflow_ref).toBe(source.workflow_ref);
  expect(JSON.parse(result.outputs.plot_spec).panels.program_attempts).toEqual([['execution', 1]]);
  const after = await (await page.request.get(`/api/plans/${source.plan_id}`, { headers })).json();
  expect(after.plan_graph).toEqual(before.plan_graph);
  await page.goto(`/?plan=${result.plan_id}`);
  await expect(page.getByText('结果复核', { exact: true })).toBeVisible();
  await expect(page.getByText('1/1 完成', { exact: true })).toBeVisible();
  await expect(page.getByText('实验执行完成，可点击右侧节点查看真实输出、报告和证据。', { exact: true })).toHaveCount(0);
  await page.locator('.react-flow__node').filter({ hasText: '实际结果复核图' }).click();
  const image = page.getByAltText('Generated Plot');
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(node => (node as HTMLImageElement).naturalWidth)).toBe(900);
  await page.screenshot({ path: testInfo.outputPath('review-plot.png'), fullPage: true });
  await page.getByRole('button', { name: '分析报告', exact: true }).click();
  await expect(page.getByRole('heading', { name: '实际结果复核', exact: true }).last()).toBeVisible();
  await page.reload();
  await page.locator('.react-flow__node').filter({ hasText: '实际结果复核图' }).click();
  await expect(page.getByAltText('Generated Plot')).toBeVisible();
});

test('状态夹具：远程 PDF 消息打开实际所属附件，刷新后仍是该文档', async ({ page }) => {
  await page.goto('/');
  const initial = await send(page, '运行 smoke 冒烟测试');
  const requestHeaders = await initial.request().allHeaders();
  const headers = { 'X-User-Id': requestHeaders['x-user-id'], 'X-Session-Id': requestHeaders['x-session-id'] };
  const upload = await page.request.post('/api/uploads', { headers, multipart: {
    file: { name: 'remote-source.pdf', mimeType: 'application/pdf', buffer: pdfFixture() },
  } });
  expect(upload.status()).toBe(201);
  const source = await upload.json();
  const created = await page.request.post('/api/plan', { headers, data: {
    intent: '阅读远程 PDF 附件', workflow: 'paper', attachments: [source.id],
  } });
  expect(created.status()).toBe(200);
  const plan = await created.json();
  expect(plan.papers[0].id).toBe(source.id);
  // 仅消息分类/下载状态使用夹具；附件保存、归属读取和 PDF 渲染使用真实 API。
  await page.route('**/api/messages', route => route.fulfill({ json: {
    type: 'plan', response: '状态夹具：当前 PDF 已保存为会话附件。', context_revision: 2,
    plan_graph: plan.plan_graph, papers: plan.papers,
  } }));
  await send(page, '阅读 https://paper.example/remote-source.pdf');
  await page.getByRole('button', { name: '阅读 remote-source.pdf', exact: true }).click();
  await expect(page.getByText('PDF reader verification', { exact: true })).toBeVisible();
  await page.reload();
  const received = page.waitForResponse(response => response.url().endsWith(`/api/uploads/${source.id}/content`));
  await page.getByRole('button', { name: '阅读 remote-source.pdf', exact: true }).click();
  await expect(page.getByText('PDF reader verification', { exact: true })).toBeVisible();
  expect((await received).status()).toBe(200);
});

test('代码产出 SVG 后可查看、下载并在刷新后恢复图像', async ({ page }, testInfo) => {
  await page.goto('/');
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100"><rect width="160" height="100" fill="#2563eb"/></svg>';
  const code = `from pathlib import Path\np = Path('/workspace/artifacts')\np.mkdir(parents=True)\n(p / 'demo.svg').write_text(${JSON.stringify(svg)})\nprint('image-output-created')`;
  await send(page, `运行以下代码\n\`\`\`python\n${code}\n\`\`\``);
  await page.getByRole('button', { name: '运行计划', exact: true }).click();
  await expect(page.getByText('2/2 完成', { exact: true })).toBeVisible({ timeout: 90_000 });
  await page.reload();
  await page.locator('.react-flow__node').filter({ hasText: 'Execute Python Experiment' }).click();
  const image = page.getByAltText('Generated Plot');
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(node => (node as HTMLImageElement).naturalWidth)).toBe(160);
  await expect.poll(async () => {
    const canvas = await page.locator('.react-flow').boundingBox();
    if (!canvas) return false;
    for (const node of await page.locator('.react-flow__node').all()) {
      const bounds = await node.boundingBox();
      if (!bounds || bounds.x < canvas.x - 1 || bounds.x + bounds.width > canvas.x + canvas.width + 1) return false;
    }
    return true;
  }).toBe(true);
  const downloaded = page.waitForEvent('download');
  await page.getByRole('link', { name: /下载 demo.svg/ }).click();
  expect((await downloaded).suggestedFilename()).toBe('demo.svg');
  await page.screenshot({ path: testInfo.outputPath('image-output.png'), fullPage: true });
});

test('上传数据后直接聊天即可分析并查看实际缺失值图表', async ({ page }, testInfo) => {
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'statistics.csv', mimeType: 'text/csv', buffer: Buffer.from('x,label\n1,a\n3,b\n,a\n'),
  });
  await expect(page.getByRole('button', { name: '移除 statistics.csv', exact: true })).toBeVisible();
  const response = await send(page, '分析数据中的均值和缺失值');
  expect((await response.json()).plan_graph.intent_type).toBe('Data_Analysis');
  await page.getByRole('button', { name: '运行计划', exact: true }).click();
  await expect(page.getByText('1/1 完成', { exact: true })).toBeVisible();
  await page.locator('.react-flow__node').filter({ hasText: 'Analyze Uploaded Dataset' }).click();
  await expect(page.getByAltText('Generated Plot')).toBeVisible();
  await expect(page.getByRole('link', { name: '下载图表', exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('data-analysis-chart.png'), fullPage: true });
});

async function interruptPlanStream(page: Page) {
  let first = true;
  const transport = { deliveredCursor: '', resumedCursor: '' };
  page.on('response', async response => {
    if (response.url().endsWith('/stream')) {
      const received = await response.headerValue('x-test-received-cursor');
      if (received !== null) transport.resumedCursor = received;
    }
  });
  await page.route('**/api/plans/*/stream', async route => {
    if (!first) {
      await route.continue();
      return;
    }
    first = false;
    // 程序和统计结果来自真实后端；仅截断传输，模拟日志之后连接断开。
    const response = await route.fetch();
    const body = await response.text();
    const frames = body.split('\n\n').filter(frame => frame.startsWith('id:'));
    const started = frames.findIndex(frame => frame.includes('"event_type": "task_started"'));
    expect(started).toBeGreaterThanOrEqual(0);
    expect(body).toContain('"event_type": "plan_completed"');
    transport.deliveredCursor = /^id: (\d+)/m.exec(frames[started])?.[1] ?? '';
    expect(transport.deliveredCursor).not.toBe('');
    await route.fulfill({
      status: 200, headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: 'retry: 10000\n\n' + frames.slice(0, started + 1).join('\n\n') + '\n\n',
    });
  });
  return transport;
}

test('实际计划在 SSE 断流后按游标重连，已打开面板恢复结果与图表', async ({ page }) => {
  const transport = await interruptPlanStream(page);
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'reconnect.csv', mimeType: 'text/csv', buffer: Buffer.from('x,label\n1,a\n3,b\n,a\n'),
  });
  await expect(page.getByRole('button', { name: '移除 reconnect.csv', exact: true })).toBeVisible();
  await send(page, '分析数据中的均值和缺失值');
  await page.getByRole('button', { name: '运行计划', exact: true }).click();
  // 轮询已看到真实完成快照，但被截断的 SSE 仍只到 started，面板此时已打开。
  await expect(page.getByText('1/1 完成', { exact: true })).toBeVisible();
  await page.locator('.react-flow__node').filter({ hasText: 'Analyze Uploaded Dataset' }).click();
  await expect.poll(() => transport.resumedCursor).toBe(transport.deliveredCursor);
  await expect(page.getByAltText('Generated Plot')).toBeVisible();
  await expect(page.getByRole('link', { name: '下载图表', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '正在执行…', exact: true })).toHaveCount(0);
});

test('SSE 断流等待重连期间切换会话，会关闭原会话连接', async ({ page }) => {
  await page.addInitScript(() => {
    const sources: EventSource[] = [];
    const Native = window.EventSource;
    window.EventSource = class extends Native {
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options);
        sources.push(this);
      }
    };
    Object.assign(window, { observedEventSources: sources });
  });
  const transport = await interruptPlanStream(page);
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'switch.csv', mimeType: 'text/csv', buffer: Buffer.from('x,label\n1,a\n3,b\n,a\n'),
  });
  await expect(page.getByRole('button', { name: '移除 switch.csv', exact: true })).toBeVisible();
  await send(page, '分析数据中的均值和缺失值');
  await page.getByRole('button', { name: '运行计划', exact: true }).click();
  await expect.poll(() => transport.deliveredCursor).not.toBe('');
  await page.getByRole('button', { name: '新建临时 Session', exact: true }).click();
  await expect.poll(() => page.evaluate(() => {
    const sources = (window as Window & { observedEventSources: EventSource[] }).observedEventSources;
    return sources.length === 1 && sources[0].readyState === EventSource.CLOSED;
  })).toBe(true);
  expect(transport.resumedCursor).toBe('');
});

async function send(page: Page, message: string) {
  await page.getByPlaceholder('直接描述实验目标，也可添加论文或数据...').fill(message);
  const response = page.waitForResponse(response =>
    response.url().endsWith('/api/messages') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  const received = await response;
  expect(received.status()).toBe(200);
  return received;
}

test('状态夹具：失败计划恢复完成后自动返回聊天报告，刷新不重复', async ({ page }) => {
  await page.goto('/');
  const created = await send(page, 'smoke');
  const payload = await created.json();
  const identifier = payload.plan_graph.id;
  let recovered = false;
  await page.route(`**/api/plans/${identifier}`, async route => {
    const graph = structuredClone(payload.plan_graph);
    graph.status = recovered ? 'completed' : 'failed';
    graph.nodes.forEach((node: { status: string; error: string }) => {
      node.status = recovered ? 'completed' : 'failed';
      node.error = recovered ? '' : '状态夹具：首次执行失败';
    });
    const answer = graph.nodes[graph.nodes.length - 1];
    answer.type = 'chat_answer';
    answer.result = recovered ? '状态夹具：恢复后测试报告' : '';
    await route.fulfill({ json: { ...payload, plan_graph: graph } });
  });
  await page.reload();
  await expect(page.getByText('状态夹具：首次执行失败', { exact: true })).toBeVisible();
  recovered = true;
  await expect(page.getByText('状态夹具：恢复后测试报告', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('状态夹具：恢复后测试报告', { exact: true })).toHaveCount(1);
});

test('状态夹具：框架分支失败后聊天仍展示完整失败报告并保持失败状态', async ({ page }) => {
  await page.goto('/');
  const response = await send(page, 'smoke');
  const payload = await response.json();
  const identifier = payload.plan_graph.id;
  await page.route(`**/api/plans/${identifier}`, async route => {
    const graph = structuredClone(payload.plan_graph);
    graph.status = 'failed';
    graph.intent_type = 'Framework_Comparison';
    graph.nodes.forEach((node: { status: string; error: string }) => {
      node.status = 'failed';
      node.error = '状态夹具：框架依赖安装失败';
    });
    const report = graph.nodes[graph.nodes.length - 1];
    report.type = 'framework_report';
    report.trigger_rule = 'all_done';
    report.status = 'completed';
    report.error = '';
    report.result = '状态夹具：完整框架报告，LangChain 已完成，LlamaIndex 安装失败，不能排名。';
    await route.fulfill({ json: { ...payload, plan_graph: graph } });
  });
  await page.reload();
  await expect(page.getByText('状态夹具：完整框架报告，LangChain 已完成，LlamaIndex 安装失败，不能排名。',
    { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('状态夹具：完整框架报告，LangChain 已完成，LlamaIndex 安装失败，不能排名。',
    { exact: true })).toHaveCount(1);
});

test('上传数据并在聊天补充列映射，刷新后无需重传或高级表单', async ({ page }, testInfo) => {
  let uploads = 0;
  page.on('request', request => {
    if (request.url().endsWith('/api/uploads') && request.method() === 'POST') uploads++;
  });
  await page.goto('/');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'ui-evaluation.csv', mimeType: 'text/csv',
    buffer: Buffer.from('id,x,outcome\na,1,yes\nb,2,no\n'),
  });
  await expect(page.getByRole('button', { name: '移除 ui-evaluation.csv', exact: true })).toBeVisible();
  const question = await send(page,
    '请用 https://github.com/pallets/itsdangerous 的真实源码，在附件数据上跑 CPU benchmark。');
  expect((await question.json()).type).toBe('clarification');
  await expect(page.getByText(/数据列为 id, x, outcome/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/数据列为 id, x, outcome/)).toBeVisible();
  const answer = await send(page, '输入列是 x，标签列是 outcome，id 是 ID 列。');
  expect(answer.request().postDataJSON().attachments).toEqual([]);
  const payload = await answer.json();
  expect(payload.type).toBe('plan');
  expect(payload.plan_graph.nodes).toHaveLength(8);
  expect(payload.plan_graph.nodes.some((node: { type: string }) => node.type === 'benchmark_preflight')).toBe(true);
  await expect(page.locator('.react-flow__node')).toHaveCount(8);
  await expect(page.getByText('8 个步骤', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '运行计划', exact: true })).toBeEnabled();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(uploads).toBe(1);
  await page.screenshot({ path: testInfo.outputPath('automatic-planning.png'), fullPage: true });
});

test('聊天创建 smoke 计划，经真实 Docker 执行并在刷新后保留结果', async ({ page }, testInfo) => {
  await page.goto('/');
  const created = await send(page, 'smoke');
  const payload = await created.json();
  expect(payload.type).toBe('plan');
  expect(payload.plan_graph.nodes).toHaveLength(2);
  await expect(page.locator('.react-flow__node')).toHaveCount(2);
  const [executed] = await Promise.all([
    page.waitForResponse(response =>
      response.url().endsWith(`/api/plans/${payload.plan_graph.id}/execute`) &&
      response.request().method() === 'POST', { timeout: 15_000 }),
    page.getByRole('button', { name: '运行计划', exact: true }).click(),
  ]);
  expect(executed.status()).toBe(202);
  await expect(page.getByText('2/2 完成', { exact: true })).toBeVisible({ timeout: 90_000 });
  await page.reload();
  await expect(page.getByText('2/2 完成', { exact: true })).toBeVisible();
  await expect(page.getByText(/运行\/安装尝试 1 次，采用 0 次修复/)).toBeVisible();
  await page.locator('.react-flow__node').filter({ hasText: 'Execute Python Experiment' }).click();
  await expect(page.getByText(/"sum":\s*15/).last()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('docker-smoke-result.png'), fullPage: true });
});

test('窄屏设置工具栏与运行按钮均可点击', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await send(page, 'smoke');
  await page.getByRole('button', { name: '运行计划', exact: true }).click({ trial: true });
  await page.getByRole('button', { name: '连接设置', exact: true }).click({ trial: true });
  await page.getByRole('button', { name: '高级实验设置', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '配置复现实验' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('mobile-settings.png'), fullPage: true });
});

test('四十条历史会话保留时，当前聊天与输入框仍可见', async ({ page }) => {
  await page.addInitScript(() => {
    const sessions = Array.from({ length: 40 }, (_, index) => ({
      id: `history-${index}`, title: `历史会话 ${index}`, createdAt: '2026-10-19', updatedAt: '2026-10-19',
      chatHistory: index === 0 ? [{ role: 'system', text: '长历史仍能阅读当前报告' }] : [],
      activePlanId: null, intentContext: null, planGraph: null,
    }));
    localStorage.setItem('openresearch-python.chat-sessions.v1', JSON.stringify({
      userId: null, activeSessionId: 'history-0', sessions,
    }));
  });
  await page.goto('/');
  await expect(page.getByRole('region', { name: '历史会话' }).getByRole('button')).toHaveCount(40);
  await expect(page.getByText('长历史仍能阅读当前报告', { exact: true })).toBeInViewport();
  await expect(page.getByPlaceholder('直接描述实验目标，也可添加论文或数据...')).toBeInViewport();
});

test('旧会话更新欢迎语并从 Transformer 推荐指令自动检索规划', async ({ page }, testInfo) => {
  const oldAnswer = '你好，我是 OpenResearch 科研助手。这条是需要保留的历史回答。';
  await page.addInitScript(answer => {
    const session = {
      id: 'legacy-welcome', title: '保留的会话', createdAt: '2026-10-10', updatedAt: '2026-10-10',
      chatHistory: [
        { role: 'system', text: '你好，我是 OpenResearch 科研助手。可上传论文、执行 Python 实验，仓库复现和数据评估请使用右上角「配置实验」。聊天和代码生成需要配置模型。' },
        { role: 'user', text: '保留我的原有记录' },
        { role: 'system', text: answer },
      ], activePlanId: null, intentContext: null, planGraph: null,
    };
    if (!localStorage.getItem('openresearch-python.chat-sessions.v1')) {
      localStorage.setItem('openresearch-python.chat-sessions.v1', JSON.stringify({
        userId: null, activeSessionId: session.id, sessions: [session],
      }));
    }
  }, oldAnswer);
  await page.goto('/');
  await expect(page.getByText(/直接说出论文或方法名称/)).toBeVisible();
  await expect(page.getByText(/仓库复现和数据评估请使用右上角/)).toHaveCount(0);
  await expect(page.getByText('保留我的原有记录', { exact: true })).toBeVisible();
  await expect(page.getByText(oldAnswer, { exact: true })).toBeVisible();
  const message = '复现一下 Transformer 论文的核心架构并跑通测试';
  await page.getByRole('button', { name: message, exact: true }).click();
  const response = await send(page, message);
  const payload = await response.json();
  expect(payload.type).toBe('plan');
  expect(response.request().postDataJSON()).toEqual({ message, attachments: [] });
  const source = payload.plan_graph.nodes.find((node: { type: string }) => node.type === 'repository_fetch');
  expect(source.inputs.repository.url).toBe('https://github.com/harvardnlp/annotated-transformer');
  expect(['ready', 'in_progress']).toContain(payload.plan_graph.status);
  await expect(page.getByRole('button', { name: '取消', exact: true })).toBeVisible();
  // 此夹具只验证分类、排队和恢复；真实模型生成与执行另由自然语言实跑验收。
  const headers = await response.request().allHeaders();
  await page.request.post(`/api/plans/${payload.plan_graph.id}/cancel`, {
    headers: { 'X-User-Id': headers['x-user-id'], 'X-Session-Id': headers['x-session-id'] },
  });
  await expect(page.locator('.react-flow__node')).toHaveCount(7);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(oldAnswer, { exact: true })).toBeVisible();
  await expect(page.getByText(/直接说出论文或方法名称/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('named-paper-plan.png'), fullPage: true });
});
