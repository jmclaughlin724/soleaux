# Workspace Topology

Preserve the live repository topology. Directory names do not create ownership; manifests, public exports, task edges, and consumers do.

## Placement

- Keep deployable endpoints in the established app surface.
- Keep reusable domain, infrastructure, UI, or configuration contracts in the established package surface for that concern.
- Keep scripts, generators, and policy enforcement under their existing tool owner.
- Do not nest an independent workspace inside another workspace or make an app import another app.

## Moving Code

Before extraction, list every import, route/runtime assumption, environment dependency, generated artifact, and test owner. Move the authoritative source once, add the narrowest public export, update direct consumers, and delete the old owner instead of adding compatibility mirrors.

Reject a new package when it only wraps one call, groups unrelated helpers, or has no cross-workspace consumer. Prefer an app-local module until the boundary earns independent ownership.

## Evidence

Verify workspace discovery, graph edges, package exports, the moved contract's focused tests, and at least one direct consumer. A passing root task does not by itself prove the new boundary is consumable.
