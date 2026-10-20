import { expect, test } from '@playwright/test';
import { codeSummary } from '../src/features/execution/codeSummary';

test('code report counts dependency and runtime operations and shows the observed interpreter', () => {
  const structured_data = JSON.stringify({
    version: 'code.recovery/v1',
    attempts: [{}, { environment: { python: '3.13.16' } }],
    decisions: [{ accepted: true, preparation: { decisions: [
      { accepted: true, runtime_after: { profile: 'cpu13' } },
    ] } }],
  });
  const text = codeSummary({ nodes: [] }, { status: 'completed', structured_data, result: 'ACTUAL_RESULT' });
  expect(text).toContain('采用 2 次修复');
  expect(text).toContain('实际 Python：3.13.16');
  expect(text).toContain('ACTUAL_RESULT');
});

test('a rejected runtime proposal keeps a failed report and does not count as a repair', () => {
  const structured_data = JSON.stringify({
    version: 'code.recovery/v1', attempts: [{}],
    decisions: [{ accepted: false, preparation: { decisions: [
      { accepted: false, runtime_after: { profile: 'cpu13' } },
    ] } }],
  });
  const text = codeSummary({ nodes: [] }, { status: 'failed', structured_data, result: 'ORIGINAL_FAILURE' });
  expect(text).toContain('程序执行失败');
  expect(text).toContain('采用 0 次修复');
  expect(text).not.toContain('实际 Python');
  expect(text).toContain('ORIGINAL_FAILURE');
});

test('truncated legacy report keeps the original output', () => {
  expect(codeSummary({ nodes: [] }, {
    status: 'completed', structured_data: '{"version":', result: 'ORIGINAL_OUTPUT',
  })).toBe('ORIGINAL_OUTPUT');
});
