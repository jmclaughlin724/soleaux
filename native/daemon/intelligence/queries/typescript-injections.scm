; Language injections for the typescript and tsx dialects: a tagged template
; literal whose tag names a known embedded language marks its template body as
; injected content. The Rust side maps the tag text through an allowlist.

(call_expression
  function: (identifier) @injection.language
  arguments: (template_string) @injection.content)
