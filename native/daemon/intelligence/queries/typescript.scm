; TypeScript structure pack, valid for both the typescript and tsx dialects of
; tree-sitter-typescript@0.23.2. Container captures are named after the node
; kind the retired fixed-kind list emitted; `@name` marks the symbol name node;
; `definition.*` and `reference.*` captures carry the definition/reference
; surface.

(function_declaration
  name: (_) @name @definition.function) @function_declaration

(generator_function_declaration
  name: (_) @name @definition.function) @generator_function_declaration

(function_signature
  name: (_) @name @definition.function) @function_signature

(class_declaration
  name: (_) @name @definition.class) @class_declaration

(abstract_class_declaration
  name: (_) @name @definition.class) @abstract_class_declaration

(method_definition
  name: (_) @name @definition.method) @method_definition

(variable_declarator
  name: (_) @name @definition.variable) @variable_declarator

(interface_declaration
  name: (_) @name @definition.interface) @interface_declaration

(type_alias_declaration
  name: (_) @name @definition.type) @type_alias_declaration

(enum_declaration
  name: (_) @name @definition.enum) @enum_declaration

(internal_module
  name: (_) @name @definition.module) @internal_module

(lexical_declaration) @lexical_declaration

(import_statement) @import_statement

(export_statement) @export_statement

(call_expression
  function: (identifier) @reference.call)

(call_expression
  function: (member_expression
    property: (property_identifier) @reference.call))
