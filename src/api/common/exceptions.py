"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.exceptions as _implementation
from controlplane_api.exceptions import ApiBaseException, NotFoundException, ConflictException, UnauthorizedException, InvalidOperationException, ForbiddenException, AuthenticationFailedException, AwsException, AzureException
__all__ = ['ApiBaseException', 'NotFoundException', 'ConflictException', 'UnauthorizedException', 'InvalidOperationException', 'ForbiddenException', 'AuthenticationFailedException', 'AwsException', 'AzureException']
sys.modules[__name__] = _implementation
