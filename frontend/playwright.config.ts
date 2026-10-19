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
    actionTimeout: 15_000,
  },
  webServer: {
    command: 'uv run --locked openresearch serve',
    cwd: projectRoot,
    url: 'http://127.0.0.1:38080/ping',
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      OPENRESEARCH_HOST: '127.0.0.1',
      OPENRESEARCH_PORT: '38080',
      OPENRESEARCH_DATA_DIR: fileURLToPath(new URL('../.data/ui-e2e', import.meta.url)),
      OPENRESEARCH_WEB_DIR: fileURLToPath(new URL('./dist', import.meta.url)),
      OPENRESEARCH_LLM_PROVIDER: 'openai',
      OPENRESEARCH_LLM_BASE_URL: 'http://127.0.0.1:38081/v1',
      OPENRESEARCH_LLM_MODEL: 'ui-intent-fixture',
      OPENRESEARCH_LLM_API_KEY: 'ui-test-only',
      OPENRESEARCH_LLM_STRUCTURED_OUTPUT: 'prompt',
      OPENRESEARCH_LLM_THINKING: 'default',
      OPENRESEARCH_API_TOKEN: '',
      OPENRESEARCH_SANDBOX_BACKEND: 'docker',
    },
  },
});
