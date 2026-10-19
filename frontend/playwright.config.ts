import { defineConfig, devices } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const projectRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:38080',
    ...devices['Desktop Chrome'],
    viewport: { width: 1600, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'uv run --locked openresearch serve',
    cwd: projectRoot,
    url: 'http://127.0.0.1:38080/ping',
    reuseExistingServer: false,
    timeout: 60_000,
    env: { OPENRESEARCH_WEB_DIR: fileURLToPath(new URL('./dist', import.meta.url)) },
  },
});
