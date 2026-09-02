"""Shared error type for the pipeline stage functions in main.py."""


class TerminalError(Exception):
    """
    A failure that retrying cannot fix (bad URL, post not found, content
    policy refusal). The stage records it in Postgres, then raises this so
    QStash stops rather than burning retries -- and, for paid stages, money.
    """
