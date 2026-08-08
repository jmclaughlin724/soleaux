; Bash structure pack for tree-sitter-bash@0.25.1. This pack carries
; definitions, command references, and command-topology ranges (pipelines,
; redirects, substitutions); execution semantics, provenance, and effect
; classification are owned by shell_policy.

(function_definition
  name: (word) @name @definition.function) @function_definition

(variable_assignment
  name: (variable_name) @name @definition.variable) @variable_assignment

(command
  name: (command_name
    (word) @reference.call))

(pipeline) @pipeline

(redirected_statement) @redirected_statement

(file_redirect) @file_redirect

(heredoc_redirect) @heredoc_redirect

(herestring_redirect) @herestring_redirect

(command_substitution) @command_substitution

(process_substitution) @process_substitution

(subshell) @subshell

(negated_command) @negated_command
