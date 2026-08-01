import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import app_server


class _JsonRequest:
    def __init__(self, method="GET", payload=None):
        self.method = method
        self._payload = payload or {}

    async def json(self):
        return self._payload


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
        self.assertNotIn("/api/initiative/profile", routes)


if __name__ == "__main__":
    unittest.main()
