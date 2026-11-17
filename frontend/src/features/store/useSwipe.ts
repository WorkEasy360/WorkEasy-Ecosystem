"use client";

import { useEffect, useRef, type PointerEvent } from "react";

/** How far a finger (or a mouse with its button held) must travel sideways to
 * count as a swipe rather than a tap, in CSS pixels. */
const SWIPE_PX = 40;

/**
 * A sideways swipe on a picture (the product page's gallery and its full-screen
 * view): pointer events only, no library. A swipe to the left shows the next
 * picture, to the right the previous one; a mostly vertical move is left to the
 * page, which keeps scrolling (the element sets `touch-action: pan-y`).
 *
 * The click a browser sends after the gesture is not a tap: `wasSwipe()` says
 * so once, for the element's own click handler.
 */
export function useSwipe(onSwipe: (step: 1 | -1) => void) {
  const start = useRef<{ x: number; y: number } | null>(null);
  const swiped = useRef(false);
  const act = useRef(onSwipe);
  useEffect(() => {
    act.current = onSwipe;
  }, [onSwipe]);

  return {
    handlers: {
      onPointerDown(e: PointerEvent<HTMLElement>) {
        if (e.pointerType === "mouse" && e.button !== 0) return;
        start.current = { x: e.clientX, y: e.clientY };
        swiped.current = false;
      },
      onPointerUp(e: PointerEvent<HTMLElement>) {
        const from = start.current;
        start.current = null;
        if (!from) return;
        const dx = e.clientX - from.x;
        const dy = e.clientY - from.y;
        if (Math.abs(dx) >= SWIPE_PX && Math.abs(dx) > Math.abs(dy)) {
          swiped.current = true;
          act.current(dx < 0 ? 1 : -1);
        }
      },
      onPointerCancel() {
        start.current = null;
      },
    },
    /** True once after a swipe, so the click that ends it does not open anything. */
    wasSwipe(): boolean {
      const was = swiped.current;
      swiped.current = false;
      return was;
    },
  };
}
