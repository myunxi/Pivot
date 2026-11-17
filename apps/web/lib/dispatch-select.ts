/**
 * Radix Select fallbacks for the dispatch form.
 *
 * While the restored value has no matching option yet, Radix mirrors the
 * select's value into an empty string and calls `onValueChange('')`. The form
 * has no "clear selection" affordance, so an empty value is always discarded
 * rather than overwriting the restored choice.
 */

import { defaultEventType, type PivotRule } from '@/lib/pivot-api';

export function isSelectable(value: string): boolean {
  return value !== '';
}

export interface RuleSelection {
  name: string;
  eventType: string;
}

export function ruleSelection(
  rules: PivotRule[],
  name: string,
): RuleSelection | null {
  if (!isSelectable(name)) return null;
  const rule = rules.find((item) => item.name === name);
  if (!rule) return null;
  return { name: rule.name, eventType: defaultEventType(rule) };
}
