import { expect, test, type Page } from '@playwright/test';

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
