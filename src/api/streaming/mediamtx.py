"""Bounded loopback-only MediaMTX v3 management transport."""
import asyncio
import json
from urllib.parse import urlsplit
from .contracts import Refused, uuid
from .http import unique


class MediaMTX:
    def __init__(self, client, origin):
        parsed = urlsplit(origin)
        if (parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','::1')
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or parsed.port is None): raise Refused('Private media API required')
        self.client, self.origin = client, origin

    async def request(self, method, path):
        async with asyncio.timeout(0.7):
            async with self.client.stream(method,self.origin+path,follow_redirects=False) as response:
                if response.status_code!=200: raise Refused('Media API unavailable')
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw)+len(chunk)>65536: raise Refused('Media API response too large')
                    raw.extend(chunk)
                return bytes(raw)

    async def sessions(self):
        value = json.loads(await self.request('GET','/v3/webrtcsessions/list?itemsPerPage=100'),object_pairs_hook=unique)
        if (type(value) is not dict or value.get('pageCount') not in (0,1) or value.get('itemCount')!=len(value.get('items',[]))
                or type(value.get('items')) is not list or len(value['items'])>22):
            raise Refused('Media API capacity mismatch')
        result = {}
        for row in value['items']:
            if type(row) is not dict: raise Refused('Invalid media session')
            identity = uuid(row.get('id'))
            if identity in result: raise Refused('Duplicate media session')
            result[identity] = row
        return result

    async def kick(self, identity):
        await self.request('POST','/v3/webrtcsessions/kick/'+uuid(identity))

    async def paths(self):
        value=json.loads(await self.request('GET','/v3/paths/list?itemsPerPage=100'),object_pairs_hook=unique)
        if (type(value) is not dict or type(value.get('items')) is not list
                or value.get('itemCount')!=len(value['items']) or len(value['items'])>2
                or value.get('pageCount') not in (0,1)):
            raise Refused('Native path capacity mismatch')
        result={}
        for row in value['items']:
            if type(row) is not dict or type(row.get('name')) is not str or row['name'] in result:
                raise Refused('Invalid native path')
            result[row['name']]=row
        return result


def compatible_path(row):
    if type(row) is not dict or row.get('online') is not True:return False
    tracks=row.get('tracks2')
    if type(tracks) is not list or len(tracks)!=2 or any(type(track) is not dict for track in tracks):return False
    by_codec={track.get('codec'):track.get('codecProps') for track in tracks}
    if set(by_codec)!={'H264','Opus'} or any(type(props) is not dict for props in by_codec.values()):return False
    # H264 Baseline forbids B frames; do not infer this from a filename/offer.
    return by_codec['H264'].get('profile')=='Baseline' and by_codec['Opus'].get('channelCount') in (1,2)
