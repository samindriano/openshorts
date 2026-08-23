"""Restart-safe ASGI entrypoint for the existing OpenShorts FastAPI app."""
import app as core
from state_guard import ClipStateGuard

app = ClipStateGuard(core.app, core)
