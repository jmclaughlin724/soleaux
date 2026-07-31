---
title: Put Interaction Logic in Event Handlers
impact: MEDIUM
impactDescription: avoids effect re-runs and duplicate side effects
tags: rerender, useEffect, events, side-effects, dependencies
---

## Put Interaction Logic in Event Handlers

If a side effect is triggered by a specific user action (submit, click, drag), run it in that event handler. Do not model the action as state + effect; it makes effects re-run on unrelated changes and can duplicate the action.

This also applies to parent notifications. Do not wait for an effect to tell a parent about a local state change if you can call the parent callback in the same event or state-updater path.

**Incorrect (event modeled as state + effect):**

```tsx
function Form() {
  const [submitted, setSubmitted] = useState(false);
  const theme = useContext(ThemeContext);

  useEffect(() => {
    if (submitted) {
      post("/api/register");
      showToast("Registered", theme);
    }
  }, [submitted, theme]);

  return <button onClick={() => setSubmitted(true)}>Submit</button>;
}
```

**Correct (do it in the handler):**

```tsx
function Form() {
  const theme = useContext(ThemeContext);

  function handleSubmit() {
    post("/api/register");
    showToast("Registered", theme);
  }

  return <button onClick={handleSubmit}>Submit</button>;
}
```

**Incorrect (notify parent from an effect):**

```tsx
function Toggle({ onChange }: { onChange: (next: boolean) => void }) {
  const [isOn, setIsOn] = useState(false);

  useEffect(() => {
    onChange(isOn);
  }, [isOn, onChange]);

  return <button onClick={() => setIsOn((value) => !value)}>Toggle</button>;
}
```

**Correct (notify parent in the same update path):**

```tsx
function Toggle({ onChange }: { onChange: (next: boolean) => void }) {
  const [isOn, setIsOn] = useState(false);

  function handleClick() {
    setIsOn((value) => {
      const next = !value;
      onChange(next);
      return next;
    });
  }

  return <button onClick={handleClick}>Toggle</button>;
}
```

Reference: [Should this code move to an event handler?](https://react.dev/learn/removing-effect-dependencies#should-this-code-move-to-an-event-handler)
