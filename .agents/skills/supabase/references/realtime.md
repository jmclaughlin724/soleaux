# Supabase Realtime Rules

Current official sources verified 2026-07-14:

- https://supabase.com/docs/guides/realtime/subscribing-to-database-changes
- https://supabase.com/docs/guides/realtime/broadcast
- https://supabase.com/docs/guides/realtime/presence
- https://supabase.com/docs/guides/realtime/authorization
- https://supabase.com/docs/guides/realtime/settings
- https://supabase.com/docs/guides/realtime/limits
- https://supabase.com/changelog?tags=realtime

Use this reference for Supabase Realtime design, implementation, review, migration, or debugging. The current Supabase changelog, topic documentation, installed client version, and live project remain authoritative.

## Select The Realtime Mechanism

1. Prefer Broadcast for new database-change subscriptions. Use a database trigger with `realtime.broadcast_changes()` when consumers need row-change payloads. Supabase recommends Broadcast for scalability and security.
2. Use `realtime.send()` for a deliberately shaped database-originated event. Confirm its current database signature before writing SQL; do not copy an argument order from remembered examples.
3. Use client Broadcast for low-latency client-to-client events. Keep payloads minimal, versioned when they cross an application boundary, and free of data the recipient is not authorized to read.
4. Use Presence only for ephemeral shared state whose join, leave, and sync semantics are actually required, such as online participants. Do not use it as durable application state.
5. Treat Postgres Changes as an available simpler option, not a forbidden API. Introduce it only when its setup advantage is worth its documented scaling limits. For an existing consumer, preserve behavior until a Broadcast migration is exercised end to end.

## Define The Channel Contract

- Give each logical audience a dedicated topic. Prefer the repository convention `scope:<id>:purpose`, such as `room:123:messages`, and avoid broad global topics that deliver events to uninterested clients.
- Use specific snake_case event names such as `message_created`; avoid generic names such as `update`, `change`, or `event`.
- Default production channels to private when authenticated or tenant data is involved. Database messages emitted by `realtime.broadcast_changes()` require private channels and Realtime Authorization.
- Treat `private: true` as an instruction to evaluate Realtime Authorization, not as authorization by itself. The matching `realtime.messages` policies must enforce the actual audience.
- Enabling the project-wide private-only setting is a remote configuration change. Verify the target project, confirm that every client uses private channels, and obtain the required authority before changing it.
- Decide whether a sender should receive its own messages and whether sends need server acknowledgement. Set Broadcast `self` and `ack` deliberately rather than enabling them in every channel.

## Own The Client Lifecycle

1. Resolve the installed Supabase client version and current client reference before choosing configuration fields, channel states, or token-refresh APIs.
2. Establish the authenticated Realtime session before joining a private channel. Keep Realtime authentication synchronized when the application session changes.
3. Create and subscribe to a channel in the framework's lifecycle owner, never directly in a render path. Prevent duplicate listeners by construction; do not depend on an undocumented string value for `channel.state`.
4. Register only the event handlers the consumer needs. Treat every payload as untrusted input and validate it against the consumer-owned contract before changing application state.
5. Observe subscription status and surface `CHANNEL_ERROR`, `TIMED_OUT`, and unexpected `CLOSED` states through the application's established error and observability paths. Supabase clients reconnect automatically; add a manual retry loop only when current client behavior and the use case require it.
6. Remove the channel through the installed client's cleanup API when the owner unmounts, disposes, signs out, or changes topic. Verify that remounting or changing identifiers leaves exactly one active subscription.

For React, keep the effect callback synchronous. Start asynchronous setup inside the effect, guard against completion after cleanup, and return a synchronous cleanup function that removes the channel.

## Broadcast From The Database

- Edit the repository's canonical declarative schema owner and generate a migration through its real workflow. Do not hand-edit a committed migration.
- Prefer a narrow trigger function per domain event when it reduces payload or fanout. Broadcast only meaningful changes and use an audience-specific topic.
- Use `realtime.broadcast_changes()` when clients should receive the standard database-change shape. Use `realtime.send()` when the event contract needs a custom payload.
- Review any `SECURITY DEFINER` trigger function as privileged code: schema qualify referenced objects, set a safe `search_path`, keep logic minimal, and preserve the repository's privilege and function-execution rules.
- Never call database-only helpers such as `realtime.send()` or `realtime.broadcast_changes()` from client code.

## Authorize Private Channels

- Authorize receiving Broadcast or Presence messages with a `SELECT` policy on `realtime.messages` using `USING`.
- Authorize sending Broadcast messages or tracking Presence with an `INSERT` policy using `WITH CHECK`. Do not write an `INSERT` policy with `USING`.
- Scope policies with `(select realtime.topic())`, the intended `realtime.messages.extension`, the authenticated identity, and the real membership or tenancy relationship. Avoid blanket `USING (true)` or `WITH CHECK (true)` in production examples unless public authenticated access is the explicit contract.
- Add indexes that support the policy's actual membership and topic lookup. Choose composite order from the predicate and workload; do not mechanically index every mentioned column.
- Keep authorization predicates direct. Supabase evaluates Realtime policies on channel join, so complex or unindexed policies increase connection latency and can reduce join throughput.
- Test receive and send permissions separately because `SELECT` and `INSERT` policies grant different capabilities.

## Migrate From Postgres Changes

1. Inventory every existing table, schema, event filter, payload assumption, ordering expectation, and active consumer.
2. Add the Broadcast trigger and least-privilege Realtime Authorization policies in the canonical schema workflow.
3. Update each configured consumer to join the exact private topic and handle the corresponding Broadcast events with cleanup and error observation.
4. Exercise inserts, updates, deletes, authorization denials, reconnection, and remount or disposal behavior. Guard against duplicate delivery if old and new subscriptions coexist during rollout.
5. Remove the old Postgres Changes subscription and publication membership only after every configured consumer is proven on Broadcast.

## Verify Completion

- Recheck the relevant Realtime changelog entries and current topic docs.
- Run the repository's schema, migration, RLS, and generated-type checks when a database trigger or policy changed.
- Exercise an allowed user and a denied user for every send and receive path.
- Verify exact topic and event names, payload validation, cleanup, remounting, sign-out, token refresh, and recovery from a network interruption.
- Check expected channel joins, message rate, payload size, and fanout against the target project's current Realtime limits before making scalability claims.
- Report any dashboard-only setting, remote project check, load test, or network interruption test that could not be completed locally.

## Boundaries

- Never expose publishable credentials beyond their intended public use, and never expose secret or `service_role` credentials in a client.
- Do not weaken RLS, use a global topic, or expand payloads merely to make a subscription work.
- Do not mutate a linked or production project without explicit authority and a verified project identity.
- Do not claim that Broadcast eliminates every delivery, ordering, connection, or capacity concern; verify the guarantees the application actually needs.
