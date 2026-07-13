"""CloakBrowser gateway: one stealth-Chromium session per provider profile,
driven imperatively by the providers (inversion of the old callback transport).

  BrowserSession(...).start() / .wait_ready()
  session.run_turn(trigger=..., capture_url=..., parse=...) -> event queue
  session.evaluate(js, arg)   # in-page fetch (models discovery, POSTs)
  run_login(...)              # one-time headed login
"""

from .session import BrowserSession, run_login

__all__ = ["BrowserSession", "run_login"]
