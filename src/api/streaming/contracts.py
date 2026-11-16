"""Owner-authenticated room decisions and bounded stream contracts."""
from dataclasses import dataclass
import ipaddress
import re
from uuid import UUID

BYTES_PER_SECOND = 391000
MAX_SESSION_SECONDS = 3600
# Fits a fresh native read plus one clock-rollover read inside the unchanged
# 0.8-second controller tick and independent one-second media watchdog.
AUTHORITY_TIMEOUT = 0.7
POLL_SECONDS = 0.25
WATCHDOG_SECONDS = 1.0


class Refused(ValueError):
    """Generic refusal; never include a token, provider error or private room."""


def uuid(value):
    try:
        parsed = UUID(value)
        if str(parsed) != value or not parsed.int: raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise Refused('Invalid stream identifier') from None


def text(value, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise Refused('Invalid stream scope')
    return value


@dataclass(frozen=True)
class Subject:
    """Construct only from the configured identity verifier, never request JSON."""
    tenant: str
    principal: str

    def __post_init__(self):
        uuid(self.tenant)
        text(self.principal, 256)


@dataclass(frozen=True)
class Scope:
    subject: Subject
    room: str
    action: str
    ip: str

    def __post_init__(self):
        if type(self.subject) is not Subject: raise Refused('Invalid subject')
        text(self.room, 512)
        if self.action not in ('publish', 'read'): raise Refused('Invalid stream action')
        try:
            if str(ipaddress.ip_address(self.ip)) != self.ip: raise ValueError()
        except ValueError: raise Refused('Invalid source IP') from None


@dataclass(frozen=True)
class Decision:
    """A current protected room Check, returned by the configured owner adapter."""
    scope: Scope
    allowed: bool
    valid_until: int
    revision: str

    def validate(self, scope, now):
        if (self.scope != scope or self.allowed is not True or type(self.valid_until) is not int
                or not now < self.valid_until <= now + MAX_SESSION_SECONDS
                or type(self.revision) is not str or not re.fullmatch(r'[a-f0-9]{64}', self.revision)):
            raise Refused('Current room authority required')


def callback(value):
    """Exact selected MediaMTX HTTP-auth wire shape; bearer only, WebRTC only."""
    keys = {'ip','user','password','token','action','path','protocol','id','query','userAgent'}
    if (type(value) is not dict or set(value) != keys or any(type(v) is not str for v in value.values())
            or value['user'] or value['password'] or value['query'] or value['protocol'] != 'webrtc'
            or not re.fullmatch(r'[a-f0-9]{64}', value['token'])
            or not re.fullmatch(r'stream/[a-f0-9]{32}/[a-f0-9]{32}', value['path'])
            or len(value['userAgent']) > 512 or value['action'] not in ('publish','read')):
        raise Refused('Invalid stream callback')
    uuid(value['id'])
    try: ipaddress.ip_address(value['ip'])
    except ValueError: raise Refused('Invalid source IP') from None
    return value
