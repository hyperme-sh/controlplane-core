"""One constrained native media configuration; no arbitrary hooks or protocols."""
import hashlib
from pathlib import Path
import re
import ssl
from urllib.parse import quote, urlsplit
from uuid import UUID
from .contracts import Refused


def configuration(value):
    host=value['publicHost']
    if type(host) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',host):
        raise Refused('Explicit media host required')
    origin=urlsplit(value['webOrigin'])
    if (origin.scheme!='https' or not origin.hostname or origin.username or origin.password
            or origin.path or origin.query or origin.fragment):raise Refused('Explicit web origin required')
    der=ssl.PEM_cert_to_DER_cert(Path(value['tlsCertificate']).read_text())
    return {
        'logLevel':'error','logDestinations':['stdout'],'dumpPackets':False,
        'readTimeout':'1s','writeTimeout':'1s','writeQueueSize':128,
        'authMethod':'http','authInternalUsers':[],
        'authHTTPAddress':'https://mediamtx:'+quote(value['callbackSecret'],safe='')+'@127.0.0.1:'+str(value['listenPort'])+'/internal/mediamtx/auth',
        'authHTTPFingerprint':hashlib.sha256(der).hexdigest(),
        'authHTTPExclude':[{'action':'api'},{'action':'metrics'}],
        'api':True,'apiAddress':'127.0.0.1:9997','apiAllowOrigins':[],
        'metrics':True,'metricsAddress':'127.0.0.1:9998',
        'pprof':False,'playback':False,'rtsp':False,'rtmp':False,'hls':False,'srt':False,'moq':False,
        'webrtc':True,'webrtcAddress':'0.0.0.0:8889','webrtcEncryption':True,
        'webrtcServerKey':value['tlsKey'],'webrtcServerCert':value['tlsCertificate'],
        'webrtcAllowOrigins':[value['webOrigin']],'webrtcTrustedProxies':[],
        'webrtcLocalUDPAddress':'0.0.0.0:8189','webrtcLocalTCPAddress':'',
        'webrtcIPsFromInterfaces':False,'webrtcAdditionalHosts':[host],'webrtcICEServers2':[],
        'pathDefaults':{'source':'publisher','maxReaders':10,'overridePublisher':False,
                        'record':False,'alwaysAvailable':False,'forward':[]},
        'paths':{'~^stream/'+UUID(value['tenant']).hex+'/[a-f0-9]{32}$':{}},
    }
