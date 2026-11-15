"""Classify retained owner/provider content without executing it."""
import re

OWNERS = ('api/', 'controlplane_api/', 'controlplane_db/', 'controlplane_events/',
          'controlplane_identity/', 'controlplane_tenancy/', 'operator_ban_store/', 'controlplane_api/')
FORBIDDEN = re.compile(r'(^|/)(stripe|controlplane_billing|payment_service)([/.\-]|$)|^api/[^\n]*(billing|payment|stripe)', re.I)


def check_member(name, data):
    if FORBIDDEN.search(name):
        raise ValueError('Hosted implementation in ' + name)
    if name.startswith(OWNERS) and name.endswith(('.py', '.json', '.toml')):
        if re.search(rb'\bstripe\b', data, re.I):
            raise ValueError('Provider implementation in ' + name)
    if name.endswith('.dist-info/METADATA') and re.search(rb'^Requires-Dist: (stripe|controlplane-billing|payment-service)\b', data, re.M | re.I):
        raise ValueError('Hosted dependency in ' + name)
