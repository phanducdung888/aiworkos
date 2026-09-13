"""Processes that deliver messages into WorkOS from outside it.

Nothing here is part of the WorkOS application, nothing here is imported by it, and nothing here
holds a database credential. A connector speaks a vendor's protocol on one side and the capture API
on the other (ADR-0058).
"""
