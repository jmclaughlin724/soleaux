; Python structure pack for tree-sitter-python@0.25.0. Container captures keep
; the node-kind vocabulary the retired fixed-kind list emitted.

(function_definition
  name: (identifier) @name @definition.function) @function_definition

(class_definition
  name: (identifier) @name @definition.class) @class_definition

(decorated_definition) @decorated_definition

(import_statement
  name: (dotted_name) @name) @import_statement

(import_statement
  name: (aliased_import
    alias: (identifier) @name)) @import_statement

(import_from_statement) @import_from_statement

(call
  function: (identifier) @reference.call)

(call
  function: (attribute
    attribute: (identifier) @reference.call))
