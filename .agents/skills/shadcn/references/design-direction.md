# Product UI Direction

Use this reference only for net-new product surfaces. The product brief and the existing consumer own visual direction; `packages/ui/src/styles/globals.css` owns shared tokens, fonts, radii, shadows, and color modes. Preserve those live contracts rather than copying values into a skill.

## Decide Before Composing

1. Identify the user's primary task and the information hierarchy that supports it.
2. Match the established density, spacing rhythm, typography, color mode, and icon treatment in the nearest production surface.
3. Select existing shadcn primitives by behavior and semantics, not by visual resemblance alone.
4. Design loading, empty, error, success, disabled, and destructive states with the same care as the happy path.
5. Validate keyboard, focus, responsive, and overflow behavior in the real consumer.

## Composition Heuristics

- Use `AlertDialog` for destructive confirmation and `Dialog` for reversible tasks or supporting detail.
- Use `Sheet` when a task needs temporary space without losing page context; preserve a usable inline or full-page alternative when the task is primary.
- Use `Table` for comparison across stable columns. Use cards or a list when each record has a distinct narrative or the layout must collapse heavily.
- Use `Command` for keyboard-oriented discovery, not as a replacement for every select or navigation surface.
- Keep summary metrics, filters, primary content, and drill-down details in a clear reading order. Avoid nesting decorative cards merely to create borders.
- Prefer semantic theme utilities and component variants. Add a shared token or variant only when multiple real consumers need the same product meaning.

For interaction architecture, accessibility, and motion decisions, route to `$react`. For registry application and theming mechanics, return to the shadcn playbook and the live target configuration.
