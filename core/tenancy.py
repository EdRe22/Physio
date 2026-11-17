"""Mandantenkontext: Welcher Therapeut ist in diesem Request aktiv?

Fail-closed: Ohne gesetzten Kontext wirft jeder Zugriff auf Patientendaten
TenantNotSet. Es gibt keinen Standardmandanten.
"""
import copy
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from django.conf import settings
from django.db import connections

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")


class TenantNotSet(Exception):
    """Zugriff auf Mandantendaten ohne aktiven Mandanten."""


@dataclass(frozen=True)
class TenantContext:
    slug: str
    alias: str
    data_key: bytes = b""

    def __repr__(self):  # Schlüssel nie in Logs
        return f"TenantContext(slug={self.slug!r})"


@dataclass(frozen=True)
class Actor:
    user_id: int | None
    label: str
    ip: str = ""


_tenant: ContextVar = ContextVar("physio_tenant", default=None)
_actor: ContextVar = ContextVar("physio_actor", default=None)


def current():
    return _tenant.get()


def require() -> TenantContext:
    ctx = _tenant.get()
    if ctx is None:
        raise TenantNotSet("Kein aktiver Mandant: Zugriff auf Patientendaten verweigert.")
    return ctx


def current_actor():
    return _actor.get()


def alias_for(slug: str) -> str:
    if not SLUG_RE.match(slug or ""):
        raise ValueError("Ungültiger Mandanten-Kürzel.")
    return f"t_{slug}"


def db_name_for(slug: str):
    engine = connections.databases["default"]["ENGINE"]
    if "sqlite3" in engine:
        return settings.DATA_DIR / "tenants" / f"{slug}.sqlite3"
    return f"{settings.TENANT_DB_PREFIX}{slug}"


def register_db(slug: str) -> str:
    """Macht die Datenbank eines Mandanten für Django ansprechbar (idempotent)."""
    alias = alias_for(slug)
    if alias in connections.databases:
        return alias
    cfg = copy.deepcopy(connections.databases["default"])
    name = db_name_for(slug)
    if "sqlite3" in cfg["ENGINE"]:
        name.parent.mkdir(parents=True, exist_ok=True)
    cfg["NAME"] = name
    connections.databases[alias] = cfg
    return alias


@contextmanager
def tenant_context(tenant, actor: Actor | None = None):
    """Aktiviert einen Mandanten (Model core.Tenant) für den Block."""
    ctx = tenant.context()
    register_db(ctx.slug)
    t_token = _tenant.set(ctx)
    a_token = _actor.set(actor) if actor is not None else None
    try:
        yield ctx
    finally:
        _tenant.reset(t_token)
        if a_token is not None:
            _actor.reset(a_token)


@contextmanager
def actor_context(actor: Actor):
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)
