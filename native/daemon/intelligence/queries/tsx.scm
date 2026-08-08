; TSX-only extension appended to the shared TypeScript pack. JSX node kinds
; exist only in the tsx dialect grammar; compiling these patterns against the
; typescript dialect would fail.

(jsx_opening_element
  name: (_) @name) @jsx_component

(jsx_self_closing_element
  name: (_) @name) @jsx_component
