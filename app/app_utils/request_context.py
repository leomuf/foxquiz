# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextvars

# ContextVars to store current request metadata across async tasks
client_ip_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "client_ip", default="127.0.0.1"
)
client_locale_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "client_locale", default="de"
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
