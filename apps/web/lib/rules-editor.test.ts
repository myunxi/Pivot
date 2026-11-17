import { describe, expect, test } from 'vitest';
import { isRulesEditorDirty, rulesSavedMessage } from './rules-editor';

describe('isRulesEditorDirty', () => {
  test('is false when the text matches the saved content', () => {
    expect(isRulesEditorDirty('{"version":1}', '{"version":1}')).toBe(false);
  });

  test('is true after an edit', () => {
    expect(isRulesEditorDirty('{"version":1,}', '{"version":1}')).toBe(true);
  });

  test('is false again when the user undoes every edit', () => {
    expect(isRulesEditorDirty('{"version":1}', '{"version":1}')).toBe(false);
  });
});

describe('rulesSavedMessage', () => {
  test('reports the reloaded rule count', () => {
    expect(rulesSavedMessage(3)).toBe(
      'Saved. 3 rules reloaded; no restart needed.',
    );
  });

  test('uses the singular form for one rule', () => {
    expect(rulesSavedMessage(1)).toBe(
      'Saved. 1 rule reloaded; no restart needed.',
    );
  });

  test('handles an empty rules file', () => {
    expect(rulesSavedMessage(0)).toBe(
      'Saved. 0 rules reloaded; no restart needed.',
    );
  });
});
