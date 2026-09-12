"""Seed topic pool. Deliberately ADVANCED / commonly-misunderstood angles,
not textbook-basic ones - things intermediate+ developers frequently get
wrong in real code. The script picks one at random each run and asks Gemini
to research that specific angle, so posts stay varied but never trivial."""

TOPICS = [
    "Why `var` inside a for-loop with setTimeout logs the wrong value, and exactly how `let` fixes it (per-iteration binding)",
    "Why array.sort() mutates the original array and silently breaks code that assumed it was pure",
    "The classic stale closure bug inside useEffect/useState and why the dependency array doesn't always save you",
    "Why comparing objects/arrays with === almost always returns false, and what developers get wrong trying to 'fix' it",
    "Why async/await inside a forEach loop does not run sequentially, even though it looks like it should",
    "How JavaScript's microtask queue can starve the macrotask queue and freeze UI updates without any infinite loop",
    "Why React state updates inside the same event handler don't reflect immediately, and how batching actually works in React 18",
    "Why passing a new inline object/array as a prop breaks React.memo even when the data is 'the same'",
    "The useEffect cleanup race condition when a fetch resolves after the component has already unmounted or props changed",
    "Why keys in a React list should never be the array index when items can reorder, insert, or delete",
    "How Next.js request memoization and the Data Cache can silently serve stale data across requests if you don't understand fetch caching rules",
    "Why a 'use client' component can still accidentally run server-only code and leak secrets to the browser",
    "Why Next.js Server Actions can be called by anyone if you don't treat them like public API endpoints",
    "The difference between structuredClone, JSON.parse(JSON.stringify()), and a spread copy - and where each one silently fails",
    "Why event listeners added in useEffect without cleanup cause memory leaks that don't show up until the app has run for a while",
    "Why `this` inside a regular function passed as a callback loses its binding, and why arrow functions aren't always the fix",
    "Why floating point math in JavaScript makes 0.1 + 0.2 !== 0.3, and the correct way to compare/round currency values",
    "How prototype chain lookups can silently shadow properties and cause bugs that only appear with certain input objects",
    "Why debounced/throttled functions defined inside a React component body get recreated every render and stop working as expected",
    "Why a Promise.all() call fails entirely if just one promise rejects, and when Promise.allSettled is the correct choice instead",
]
