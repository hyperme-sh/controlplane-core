"""Finish a source-authorized retirement using its durable reference set."""
from api.domain.media_allocation_grant import AllocationRejected


class MediaRetirement:
    def __init__(self, authority, objects):
        # Both adapters must operate on one registry and tenant lock domain.
        if authority.store is not objects.store:
            raise ValueError('Retirement registry mismatch')
        self.authority,self.objects=authority,objects

    async def retire(self, request, *, object,owner,command,cancel):
        # begin authenticates the fresh source envelope and commits the permanent
        # command before any provider operation. Failed cleanup remains retryable.
        phase=await self.authority.begin(request,object=object,owner=owner,command=command,cancel=cancel)
        if type(phase) is not int or phase not in (1,2):
            raise AllocationRejected('Retirement state unavailable')
        cancel()
        if phase==1:
            await self.objects.cleanup(object=object,command=command,guard=cancel)
        cancel()
        return {'accepted':True,'command':command,'cleanup':'complete'}
