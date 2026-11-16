// Adapted from MediaMTX e65f9411a56b19076a0dfde1fb4a59414bdf642c
// internal/servers/webrtc/server_test.go, MIT licensed, copyright MediaMTX authors.
// CI-only native protocol contract; the upstream PathManager is a test double.
package webrtc
import (
 "context"
 "net/http"
 "net/url"
 "testing"
 "time"
 pwebrtc "github.com/pion/webrtc/v4"
 "github.com/pion/rtp"
 "github.com/stretchr/testify/require"
 "github.com/bluenviron/mediamtx/internal/auth"
 "github.com/bluenviron/mediamtx/internal/conf"
 "github.com/bluenviron/mediamtx/internal/defs"
 "github.com/bluenviron/mediamtx/internal/protocols/webrtc"
 "github.com/bluenviron/mediamtx/internal/protocols/whip"
 "github.com/bluenviron/mediamtx/internal/stream"
 "github.com/bluenviron/mediamtx/internal/test"
)
func TestCoreStreamKickBound(t *testing.T) {
	pathManager := &test.PathManager{
		FindPathConfImpl: func(_ defs.PathFindPathConfReq) (*defs.PathFindPathConfRes, error) {
			expiresAt := time.Now().Add(30 * time.Second)
			return &defs.PathFindPathConfRes{
				Conf: &conf.Path{},
				AuthRes: &auth.Result{
					User:      "somebody",
					ExpiresAt: &expiresAt,
				},
			}, nil
		},
		AddPublisherImpl: func(req defs.PathAddPublisherReq) (*defs.PathAddPublisherRes, error) {
			require.True(t, req.AccessRequest.SkipAuth)
			strm := &stream.Stream{
				OrigDesc:          req.Desc,
				WriteQueueSize:    512,
				RTPMaxPayloadSize: 1450,
				Parent:            test.NilLogger,
			}
			require.NoError(t, strm.Initialize())
			subStream := &stream.SubStream{Stream: strm, UseRTPPackets: true}
			require.NoError(t, subStream.Initialize())
			return &defs.PathAddPublisherRes{Path: &dummyPath{}, SubStream: subStream}, nil
		},
	}
	s := &Server{
		Address:            "127.0.0.1:8886",
		ReadTimeout:        conf.Duration(10 * time.Second),
		WriteTimeout:       conf.Duration(10 * time.Second),
		LocalUDPAddress:    "127.0.0.1:8887",
		LocalTCPAddress:    "127.0.0.1:8887",
		IPsFromInterfaces:  true,
		STUNGatherTimeout:  conf.Duration(5 * time.Second),
		HandshakeTimeout:   conf.Duration(10 * time.Second),
		TrackGatherTimeout: conf.Duration(2 * time.Second),
		PathManager:        pathManager,
		Parent:             test.NilLogger,
	}
	require.NoError(t, s.Initialize())
	defer s.Close()

	u, err := url.Parse("http://localhost:8886/teststream/whip")
	require.NoError(t, err)
	track := &webrtc.OutboundTrack{Caps: pwebrtc.RTPCodecCapability{
		MimeType:    pwebrtc.MimeTypeH264,
		ClockRate:   90000,
		SDPFmtpLine: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
	}}
	client := &whip.Client{
		HTTPClient:     http.DefaultClient,
		URL:            u,
		Publish:        true,
		OutboundTracks: []*webrtc.OutboundTrack{track},
		Log:            test.NilLogger,
	}
	require.NoError(t, client.Initialize(context.Background()))
	defer client.Close() //nolint:errcheck
	require.NoError(t, track.WriteRTP(&rtp.Packet{
		Version: 2, Marker: true, PayloadType: 96, SequenceNumber: 1, Payload: []byte{1},
	}))

	require.Eventually(t, func() bool {
		list, listErr := s.APISessionsList()
		return listErr == nil && len(list.Items) == 1 && list.Items[0].State == defs.APIWebRTCSessionStatePublish
	}, 2*time.Second, 20*time.Millisecond)
	list, err := s.APISessionsList()
	require.NoError(t, err)
	require.Equal(t, "somebody", list.Items[0].User)
	require.NotNil(t, list.Items[0].ExpiresAt)
	start := time.Now()
	require.NoError(t, s.APISessionsKick(list.Items[0].ID))
	require.Eventually(t, func() bool {
		current, err := s.APISessionsList()
		return err == nil && len(current.Items) == 0
	}, 1500*time.Millisecond, 10*time.Millisecond)
	require.Less(t, time.Since(start), 2*time.Second)
}
