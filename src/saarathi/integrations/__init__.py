"""Integration- and identity-aware worker routing (§12A, ADR-0008).

Service names (``gmail``, ``microsoft.outlook``, ``github``...) are opaque strings here. Nothing in this
package or in the scheduler knows what a particular service does.
"""
