import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import app_server
from sovereign.protocol import protocol_tree_envelope
from sovereign.session import Session
from sovereign.transport import HttpTransportAdapter


class _JsonRequest:
    def __init__(self, method="GET", payload=None):
        self.method = method
        self._payload = payload or {}

    async def json(self):
        return self._payload


class _HttpClient:
    def __init__(self, profile):
        self.profile = profile
        self.join_payload = None

    def get_json(self, url, timeout=5):
        return protocol_tree_envelope(self.profile)

    def post_json(self, url, payload, timeout=5):
        if url.endswith("/p2p/join"):
            self.join_payload = payload
        return {
            "status": "ok",
            "topic_members": {self.profile.uuid: ["http://peer"]},
        }


class CoreProfileTests(unittest.TestCase):
    def test_zero_application_host_owns_profile_routes_and_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = app_server.create_runtime(8470, {
                "app_module": None,
                "storage_file": str(Path(tmp) / "state.json"),
            })
            routes = {
                route.path: route.endpoint
                for route in app_server.build_core_routes(runtime)
            }

            updated = asyncio.run(routes["/api/core/profile"](_JsonRequest(
                "POST", {"name": "Alice"},
            )))
            image = b"\x89PNG\r\n\x1a\nminimal"
            blob_id = runtime.blob_store.write_blob(image)
            avatar = asyncio.run(routes["/api/core/profile/avatar"](_JsonRequest(
                "POST",
                {"attachment": {
                    "id": "avatar-1",
                    "role": "avatar",
                    "blob_id": blob_id,
                    "name": "alice.png",
                    "size": len(image),
                    "mime": "image/png",
                }},
            )))
            rendered = asyncio.run(routes["/api/core/profile"](_JsonRequest()))

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(avatar.status_code, 200)
        payload = json.loads(rendered.body)
        self.assertEqual(payload["display_name"], "Alice")
        self.assertEqual(payload["picture"], f"/api/blob/{blob_id}")
        self.assertEqual(payload["avatar"]["role"], "avatar")
        self.assertEqual(payload["profile"]["data"]["type"], "shared_user_profile")
        self.assertIn("/api/core/profile/avatar", routes)
        self.assertNotIn("/api/kanban/profile", routes)

    def test_zero_application_direct_join_accepts_profile_as_peer_cache_only(self):
        local = Session("http://local")
        peer = Session("http://peer")
        peer.set_identity("Bob")
        peer_profile = peer.identity
        local_profile_uuid = local.identity.uuid
        http = _HttpClient(peer_profile)
        adapter = HttpTransportAdapter(local, http, logger=lambda _: None)

        result = adapter.join_discussion("http://peer", peer_profile.uuid)

        self.assertEqual(result["status"], "ok")
        self.assertNotIn(peer_profile.uuid, local.protocol.index)
        self.assertIn(local_profile_uuid, local.protocol.index)
        cached = local.get_cached_peer_subtree("http://peer", peer_profile.uuid)
        self.assertEqual(cached.data["display_name"], "Bob")
        self.assertIn(local_profile_uuid, http.join_payload["topic_uuids"])


if __name__ == "__main__":
    unittest.main()
