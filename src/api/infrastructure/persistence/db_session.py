"""Compatibility alias for controlplane_tenancy.db_session."""
import sys
from controlplane_tenancy import db_session as _implementation
from controlplane_tenancy.db_session import configure_session_maker, get_session_maker, get_db_session, set_db_session, reset_db_session

__all__ = ['configure_session_maker', 'get_session_maker', 'get_db_session', 'set_db_session', 'reset_db_session']

sys.modules[__name__] = _implementation
