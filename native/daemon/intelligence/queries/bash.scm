; Bash structure pack for tree-sitter-bash@0.25.1. Command topology (pipelines,
; redirects, substitutions) stays owned by extract_shell_commands; this pack
; carries definitions and command references.

(function_definition
  name: (word) @name @definition.function) @function_definition

(variable_assignment
  name: (variable_name) @name @definition.variable) @variable_assignment

(command
  name: (command_name
    (word) @reference.call))
