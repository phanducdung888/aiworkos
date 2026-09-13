"""The IMAP connector: a separate process that delivers email into WorkOS.

Not part of the WorkOS application and deliberately not importable from it. It holds no database
credential, no business rule and no AI logic — it receives messages, normalises them, and posts
them to the capture API under the contract in ADR-0058.
"""
