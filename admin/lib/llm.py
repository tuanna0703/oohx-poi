"""Shared UI for the LLM circuit breaker.

Rendered on both the Dashboard and the Dedupe Queue so an operator cannot miss
a paused pipeline whichever page they land on.
"""

from __future__ import annotations

import streamlit as st

from admin.lib.api import get_json, post_json


def breaker_banner() -> bool:
    """Show a red banner + Resume button when dedupe is paused.

    Returns True when paused, so a caller can skip work that assumes the
    pipeline is live.

    Dedupe stops rather than merging without the resolver: a NEEDS_LLM pair
    handled without it becomes two separate masters marked ``merged``, and
    nothing revisits those rows. Paused is recoverable; degraded is not.
    """
    try:
        status = get_json("/api/v1/admin/llm/status")
    except Exception as exc:  # the page must still render if the API is down
        st.info(f"Could not read LLM status: {exc}")
        return False

    if not status.get("paused"):
        return False

    st.error(
        "**Dedupe is paused — the Anthropic API rejected us.**\n\n"
        f"Since `{status.get('since')}`\n\n"
        f"Reason: `{status.get('reason')}`\n\n"
        "Nothing is being merged. Top up the Anthropic credit balance, then "
        "press Resume — passes restart on the next scheduled tick."
    )
    if st.button("▶︎ Resume dedupe", type="primary", key="llm_resume"):
        try:
            post_json("/api/v1/admin/llm/resume")
        except Exception as exc:
            st.warning(f"Resume failed: {exc}")
        else:
            st.success("Resumed. The next scheduled pass will run.")
            st.rerun()
    return True
