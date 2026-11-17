/**
 * Small pure pieces of the rules.json editor so its state rules are testable
 * without mounting the dialog.
 */

export function isRulesEditorDirty(
  content: string,
  savedContent: string,
): boolean {
  return content !== savedContent;
}

export function rulesSavedMessage(ruleCount: number): string {
  return `Saved. ${ruleCount} rule${ruleCount === 1 ? '' : 's'} reloaded; no restart needed.`;
}
