import { type RefObject, useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * Motion helpers. Everything here is a no-op when the visitor asked the OS
 * for reduced motion, or the browser can't animate (jsdom in tests): the UI
 * then shows final values straight away.
 */
export function motionAllowed(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches &&
    typeof Element.prototype.animate === "function"
  );
}

const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

/** A number that counts from its previous value to the new one. */
export function useCountUp(value: number, duration = 700): number {
  const [shown, setShown] = useState(() => (motionAllowed() ? 0 : value));
  const from = useRef(shown);
  useEffect(() => {
    if (!motionAllowed()) {
      setShown(value);
      return;
    }
    const start = performance.now();
    const origin = from.current;
    let frame = requestAnimationFrame(function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const next = Math.round(origin + (value - origin) * easeOut(t));
      from.current = next;
      setShown(next);
      if (t < 1) frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [value, duration]);
  return shown;
}

/**
 * FLIP for a list: children with a ``data-flip`` key glide to their new
 * position when the order changes, and new ones slide in.
 */
export function useFlip(container: RefObject<HTMLElement>, deps: unknown[]): void {
  const positions = useRef(new Map<string, DOMRect>());
  const mounted = useRef(false);
  useLayoutEffect(() => {
    const root = container.current;
    if (!root) return;
    const items = Array.from(root.querySelectorAll<HTMLElement>("[data-flip]"));
    const next = new Map<string, DOMRect>();
    for (const el of items) next.set(el.dataset.flip!, el.getBoundingClientRect());

    // Only a few new rows among familiar ones count as arrivals; a filter
    // change that swaps the whole list just appears
    const added = items.filter((el) => !positions.current.has(el.dataset.flip!)).length;
    const arrivals = mounted.current && added <= 2 && added < items.length;

    if (motionAllowed()) {
      for (const el of items) {
        const key = el.dataset.flip!;
        const before = positions.current.get(key);
        const after = next.get(key)!;
        if (before) {
          const dx = before.left - after.left;
          const dy = before.top - after.top;
          if (dx || dy) {
            el.animate(
              [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0, 0)" }],
              { duration: 420, easing: "cubic-bezier(.2,.8,.2,1)" },
            );
          }
        } else if (arrivals) {
          // Arrived while the list was on screen: make it noticeable
          el.animate(
            [
              { opacity: 0, transform: "translateX(-16px) scale(.98)" },
              { opacity: 1, transform: "none" },
            ],
            { duration: 480, easing: "cubic-bezier(.2,.8,.2,1)" },
          );
          el.classList.add("arrived");
          setTimeout(() => el.classList.remove("arrived"), 2400);
        }
      }
    }
    positions.current = next;
    mounted.current = true;
  }, deps);
}

/** Elapsed seconds since ``since`` (ms), ticking every 100 ms while active. */
export function useStopwatch(since: number | null, running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null || !running) return;
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [since, running]);
  return since === null ? 0 : Math.max(0, (now - since) / 1000);
}

/**
 * 3D tilt: elements marked ``data-tilt`` lean toward the pointer while a glare
 * follows it (``data-tilt="soft"`` leans less, for wide panels), and the
 * backdrop drifts a little against the pointer. One listener for the page,
 * and only for a mouse or trackpad: on touch everything stays flat.
 */
export function useTilt(): void {
  useEffect(() => {
    if (!motionAllowed() || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    const root = document.documentElement;
    let active: HTMLElement | null = null;
    let last: PointerEvent | null = null;
    let frame = 0;

    const release = (el: HTMLElement) => {
      el.classList.remove("tilting");
      el.style.removeProperty("--rx");
      el.style.removeProperty("--ry");
    };
    const apply = () => {
      frame = 0;
      if (!last) return;
      const { clientX, clientY, target } = last;
      root.style.setProperty("--px", ((clientX / window.innerWidth) * 2 - 1).toFixed(3));
      root.style.setProperty("--py", ((clientY / window.innerHeight) * 2 - 1).toFixed(3));
      const el = target instanceof Element ? target.closest<HTMLElement>("[data-tilt]") : null;
      if (active && active !== el) release(active);
      active = el;
      if (!el) return;
      const box = el.getBoundingClientRect();
      const x = (clientX - box.left) / box.width;
      const y = (clientY - box.top) / box.height;
      const max = el.dataset.tilt === "soft" ? 2.5 : 9;
      el.classList.add("tilting");
      el.style.setProperty("--rx", `${((0.5 - y) * 2 * max).toFixed(2)}deg`);
      el.style.setProperty("--ry", `${((x - 0.5) * 2 * max).toFixed(2)}deg`);
      el.style.setProperty("--gx", `${(x * 100).toFixed(1)}%`);
      el.style.setProperty("--gy", `${(y * 100).toFixed(1)}%`);
    };
    const onMove = (e: PointerEvent) => {
      last = e;
      if (!frame) frame = requestAnimationFrame(apply);
    };
    const onLeave = () => {
      if (active) release(active);
      active = null;
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onLeave);
    return () => {
      window.removeEventListener("pointermove", onMove);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      cancelAnimationFrame(frame);
      onLeave();
    };
  }, []);
}
