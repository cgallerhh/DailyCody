# Technische TickTick-Anfrage – Entwurf, nicht versendet

Subject: Documented read-only Open API access to all Inbox tasks

Hello TickTick team,

We are implementing a personal morning briefing using a separate OAuth app
with only `tasks:read`. We need to enumerate every open task in all accessible
lists, including the Inbox, undated tasks and older overdue tasks, without
creating, editing, moving or completing any task.

The documented project-list/project-data GET flow has not established Inbox
coverage. `GET /open/v1/project/inbox/data` returned an object with a tasks array
but no project identity. An additional unpaginated project-list GET also did not
provide an adapter-valid Inbox identity. This must not be treated as proof that
the Inbox is empty. We are not including tokens, credentials or task content.

Please confirm:

1. Which documented endpoint enumerates all open Inbox tasks, including undated
   tasks, with `tasks:read`? Is the literal `inbox` alias supported for the
   project-data GET endpoint, or only for the dated task/undone endpoint?
2. If an account-specific Inbox project ID is needed, which documented read-only
   endpoint discovers it without accessing an internal web session or writing
   a temporary task?
3. Can task/search enumerate all open Inbox tasks with no keywords or dates?
   What are its result limits and pagination rules? How can task/filter's
   documented 200-task limit be traversed without missing undated tasks?
4. How does a valid empty Inbox response differ from an unsupported project ID
   or incomplete access, and is that distinction documented?

The date-required task/undone endpoint alone cannot establish complete coverage
of undated tasks. Please provide the supported complete read-only contract so
we can validate the integration without speculative reauthorization attempts.

Thank you.
