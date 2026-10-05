"""integration/automation - generic, bounded, event- and schedule-driven rules.

A rule is: a trigger (an event filter, or a schedule), a bounded condition
set over the event's public fields and payload, and one to five registered
actions. Nothing here knows what a CRM, a ledger or a stock item is - that
lives in the products' events and in the customer's configuration (Task 35).

Deliberately *not* a workflow engine: no branching, no loops, no waits, no
variables, no expression language, no visual canvas. One trigger, one
condition set, a short list of actions, each executed exactly as the rule
version pinned at trigger time says.
"""
