# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

import contextvars

# ContextVars to store current request metadata across async tasks
client_ip_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "client_ip", default="127.0.0.1"
)
client_locale_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "client_locale", default="en"
)
anonymous_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "anonymous_id", default="anon-default"
)


def get_client_ip() -> str:
    """Get the current request client IP address from ContextVar."""
    return client_ip_ctx.get()


def get_client_locale() -> str:
    """Get the current request client locale from ContextVar."""
    return client_locale_ctx.get()


def get_anonymous_id() -> str:
    """Get the current request anonymous visitor session ID from ContextVar."""
    return anonymous_id_ctx.get()
