import { Button as ButtonPrimitive } from '@base-ui/react/button';
import { cn } from '@/lib/utils';

type ButtonProps = ButtonPrimitive.Props & { variant?: 'default' | 'outline' };

function Button({ className, variant = 'default', ...props }: ButtonProps) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(
        "group/button inline-flex shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        'h-8 gap-1.5 px-2.5',
        variant === 'outline'
          ? 'border-border bg-background hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50'
          : 'bg-primary text-primary-foreground hover:bg-primary/80',
        className,
      )}
      {...props}
    />
  );
}

export { Button };
