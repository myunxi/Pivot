import { APP_NAME } from '@/lib/branding';
import { cn } from '@/lib/utils';

/** Text wordmark for the app chrome (sidebar header, mobile top bar). */
export function BrandWordmark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'select-none font-mono text-lg font-semibold leading-none tracking-tight',
        className,
      )}
    >
      {APP_NAME}
    </span>
  );
}
