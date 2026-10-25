import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const variants = cva("inline-flex h-9 px-4 py-2 items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50", {
  variants: {variant: {default: "bg-primary text-primary-foreground hover:opacity-90", outline: "border border-border bg-background hover:bg-muted"}},
  defaultVariants: {variant: "default"}
});
type Props = React.ComponentProps<"button"> & VariantProps<typeof variants> & {asChild?: boolean};
export function Button({className, variant, asChild = false, ...props}: Props) {
  const Component = asChild ? Slot : "button";
  return <Component className={cn(variants({variant}), className)} {...props} />;
}
