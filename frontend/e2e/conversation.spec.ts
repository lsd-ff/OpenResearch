import { expect, test, type Page } from '@playwright/test';
import { pdfFixture } from './pdfFixture';

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

async function send(page: Page, message: string) {
  await page.getByPlaceholder('直接描述实验目标，也可添加论文或数据...').fill(message);
  const response = page.waitForResponse(response =>
    response.url().endsWith('/api/messages') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  const received = await response;
  expect(received.status()).toBe(200);
  return received;
}

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
  expect(payload.plan_graph.nodes).toHaveLength(6);
  await expect(page.locator('.react-flow__node')).toHaveCount(6);
  await expect(page.getByText('6 个步骤', { exact: true })).toBeVisible();
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
  await expect(page.getByText('实验执行完成，可点击右侧节点查看真实输出、报告和证据。')).toBeVisible();
  await page.locator('.react-flow__node').filter({ hasText: 'Execute Python Experiment' }).click();
  await expect(page.getByText(/"sum":\s*15/)).toBeVisible();
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
  await expect(page.locator('.react-flow__node')).toHaveCount(6);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(oldAnswer, { exact: true })).toBeVisible();
  await expect(page.getByText(/直接说出论文或方法名称/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('named-paper-plan.png'), fullPage: true });
});
