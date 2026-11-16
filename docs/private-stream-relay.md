# Private stream fallback

Core #77 supplies an explicit optional relay, not the default s3r.ch transport.
Direct P2P and client-direct IPFS recordings are post-v0/pre-v1 work under
s3r.ch #569/#570. No media or recording bytes enter Core/PostgreSQL. The committed
policy disables streaming and provisioning. There is no default source, credential,
DDL, tenant bootstrap, recording store, pinning service or registration in the
existing storage application.

## Admission and owner binding

`api.streaming.owner.create_app` is an uninstalled private transport around the
owner's **native** authenticated session resolver and current room Check. A binding
must declare its protected source ready; the factory supplies no public Gun or
caller-selected-owner fallback. Identity returns a `Subject(tenant, principal)`.
Every Check must return the exact `Scope`, an affirmative decision, a finite
absolute deadline and its protected revision. Implement these calls inside the
existing identity/room owner; an HTTP mock is not that binding.

`ProtectedAuthority` calls that fixed HTTPS endpoint with a service credential,
request nonce and authenticated bearer on identity requests. The owner signs its
fresh exact response with Ed25519. Replies bind operation, nonce, tenant,
principal, room, action and source IP. Core rejects mismatches, stale timestamps,
invalid signatures and missing authority, and never caches an allow. A fresh Check
is required at reservation, native callback and each controller poll. The private
owner endpoint is `/internal/streams/authority`; its strict wire shape and both
transport directions are implemented in `authority.py`/`owner.py`.

`POST /streams/reserve` accepts only `room`, `action`, `seconds` and explicit
`fallback: true`; identity comes from that native owner resolver. Forwarded IP
headers are not trusted. Deploy a direct TLS endpoint or an independently reviewed
source-IP-preserving ingress, not a proxy that turns every browser into localhost.
No default/failure branch invokes this endpoint for direct P2P traffic.

The response carries an opaque random bearer, exact native path, reservation ID
and finite deadline. PostgreSQL stores only its SHA-256 hash. A token must be
consumed within ten seconds and only once, even if the same session ID retries.
`/internal/mediamtx/auth` requires a loopback caller plus its separate Basic secret,
WebRTC, exact path/action/IP and a fresh owner Check. Responses always include
finite `expireAt`; no successful empty/null expiry response exists.

## PostgreSQL and cost

The digest-verified schema from controlplane-db #59 is an explicit owner command,
never startup DDL. Runtime requires the preexisting tenant and enabled generation.
The tenant lock serializes one publisher per room, two streams per tenant, ten
readers per stream and eleven outstanding sessions per source IP. Every reader
requires an active publisher and cannot outlive it. The service refuses policy
above **32,000,000,000 bytes per tenant per UTC day**. Reserve 391,000 bytes/second
(3 Mbit/s video plus 128 kbit/s audio) up front; sessions last at most an hour and
cannot cross midnight. Closing/restarting never refunds the day's reservation.

This is conservative relay admission accounting, not an Azure invoice meter.
Native session counters detect exhausted reservations and excessive combined
rate with a 64 KiB burst allowance; polling is not a kernel packet policer. Infra
#1703 owns aggregate external TURN/provider/seeding accounting and hard network
limits including protocol overhead and bounded overshoot. Until those bindings
are qualified, provisioning stays disabled and no additional TURN provider is
configured. Direct client traffic is outside this ledger. Funding options belong
to s3r.ch #571; this code cannot charge a user or grant advertising credit.

## Controller and native process

The controller checks native path/session mapping, current room authority,
publisher presence, expiry, counters and H264 Baseline plus Opus. Baseline excludes
B frames; mismatched/unknown codecs are refused. The controller closes readers
when their publisher stops. API/metrics bind numeric loopback; HTTP authorization
is used only for WebRTC, with private management excluded inside that boundary.
Recording, transcoding, HLS and alternate ingest protocols are absent/disabled.

A standalone standard-library watchdog owns the native process group. Only a
complete controller pass renews its one-second monotonic timer. Stale/backlogged
heartbeats, controller exit, blocked checks or failed native management stop
renewal. The guard kills its own process group, and Linux parent-death signaling
kills the media leader if the guard crashes. The guard does not import the legacy
application. Controller operations have a bounded timeout; an exception stops the
service rather than continuing with stale permission. CI contracts enforce the
two-second guard/kick bound. They do not promise scheduling deadlines under a
frozen kernel or privileged interference.

A PostgreSQL session advisory lock permits one controller per tenant. Restart
waits out the previous guard before reconciling old rows, preserving all charges.
A changed generation or policy loss stops authority. Actual media process restart,
room grant revocation, source loss, byte overshoot and publisher-stop cases belong
to Infra #1311's scheduled adapter, with declared identity/room/grant fixtures.

`python -m api.streaming.runtime PRIVATE_CONFIG` is explicit composition only.
It requires existing private TLS/key/source/DB material and an exact safe native
configuration, starts its private callback/public admission listener and the owned
MediaMTX child, and tears down on failure. Native binaries run by version/channel;
exact source pins below identify tested bytes, not deployed service versions.

## Qualification boundary

Offline unit contracts use named identity/store/media doubles. CI additionally
uses disposable PostgreSQL and native MediaMTX source
`e65f9411a56b19076a0dfde1fb4a59414bdf642c` (1.21.4) for H264/Opus, finite expiration
and API kick contracts. The upstream native path manager is a test double; that
job is not a real room acceptance claim. The scheduled Infra adapter must use
real declared owner fixtures and fail closed when they are missing. No local
shared-host runs, provider provisioning, new credentials or frontend changes are
part of this implementation.


Native authority requests have a 700 ms deadline, allowing a fresh owner snapshot
and a bounded reread when the evaluation second rolls over. The s3r.ch binding
batches only requests queued before a snapshot starts; it never reuses completed
snapshots or allow decisions. Media management calls retain their 250 ms deadline,
the controller tick stays bounded to 800 ms, and the independent one-second
watchdog remains unchanged. This does not extend the two-second shutdown bound.
