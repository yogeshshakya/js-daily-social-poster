"""Seed topic pool. The script picks one at random each run and asks Gemini
to write fresh content around it, so posts stay varied even though the
underlying subject areas repeat over weeks/months."""

TOPICS = [
    "Latest JavaScript language feature (ES2024/ES2025) developers should know",
    "A common JavaScript performance mistake and how to fix it",
    "How the JavaScript event loop / microtask queue actually works",
    "A useful but underused native JavaScript array/object method",
    "Memory leaks in JavaScript closures and how to avoid them",
    "Debouncing vs throttling in JavaScript - when to use which",
    "React re-render optimization (memo, useMemo, useCallback) done right",
    "A React hooks mistake beginners make and the correct pattern",
    "React Server Components vs Client Components - practical guidance",
    "State management in React in 2026 - when you do (and don't) need a library",
    "React performance profiling with the React DevTools Profiler",
    "Next.js App Router data fetching best practices",
    "Next.js caching model (fetch cache, route cache, full route cache) explained simply",
    "Next.js image and font optimization best practices",
    "Choosing between Server Actions and API routes in Next.js",
    "Improving Core Web Vitals (LCP/INP/CLS) in a React/Next.js app",
    "Bundle size optimization tips for modern JS/React/Next.js apps",
    "TypeScript tips that make React/Next.js code safer",
    "Common anti-patterns in useEffect and cleaner alternatives",
    "Edge runtime vs Node.js runtime in Next.js - practical differences",
]
