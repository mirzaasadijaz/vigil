import React, { useEffect, useRef, useState } from "react";

export function useInView(threshold = 0.3): [React.RefObject<any>, boolean] {
  const ref = useRef<any>(null);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
        }
      },
      { threshold }
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold]);

  return [ref, isVisible];
}
