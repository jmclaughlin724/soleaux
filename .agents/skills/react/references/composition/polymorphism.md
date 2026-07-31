---
title: Polymorphism
description: Decide whether a React component should support more than one rendered element.
type: reference
summary: Prefer stable semantics; delegate real polymorphism to the installed primitive contract.
prerequisites:
  - composition.md
  - types.md
related:
  - as-child.md
  - accessibility.md
---

# Polymorphism

Polymorphism is a public API and type contract, not a styling shortcut. Most application components should render one semantic element and expose variants for visual differences.

## Choose the Narrowest API

| Requirement | Preferred shape |
| --- | --- |
| Same element, different appearance | Variant or `className` |
| Link with button styling | Link plus the existing variant function |
| Installed primitive delegates to a child | Its documented `render` or `asChild` API |
| Public library genuinely supports several intrinsic elements | A deliberately typed `as` contract with ref and prop tests |

Do not add `@radix-ui/react-slot` or another polymorphism helper from this reference. Reuse the component library's installed composition owner.

## Contract Checks

Before exposing polymorphism, prove that:

- invalid element-specific props are rejected;
- the ref type follows the rendered element;
- required roles and keyboard behavior survive substitution;
- event and class merging order is defined; and
- consumers cannot create nested interactive elements accidentally.

If the generic types become harder to understand than two explicit components, export the explicit components. Semantic clarity is more valuable than a universal `as` prop.
