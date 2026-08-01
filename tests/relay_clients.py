"""Several clients on one relay folder, for tests that need collaboration.

Replaces `MemoryHttpClient`, which delivered a peer's message by calling the
other runtime's handler in-process. That was fast, and it exercised a route
no user takes. Here clients only ever see each other's work through a shared
folder: one publishes, the other polls. The cost is that a test has to say
when a cycle happens - `sync()` - and the gain is that what the tests prove
is what ships.

Nothing here waits on the clock. `publish_due_topics` and `poll_and_apply`
are the same two calls the channel tick makes; the tick's only other job is
deciding when to make them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import app_server


def relay_runtime(test, port: int, relay_root: str, app: str = "initiative"):
    """One runtime with one relay target, publishing under its own identity.

    Attaches `relay`, `relay_target` and `peer_addr` - the last being how the
    other clients' registries name this one. A relay peer is a publication
    identity, not an address anybody can reach.
    """
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    config = app_server.load_config(None, app)
    config["storage_file"] = str(Path(directory.name) / f"{port}.json")
    config["relay_state_directory"] = str(Path(directory.name) / "relay")
    runtime = app_server.create_runtime(port, config)
    runtime._test_tmp = directory
    created = runtime.relay_manager.create_target({
        "name": f"relay {port}", "backend": "local", "root": relay_root,
    })
    if created.status != "ok":
        raise RuntimeError(created.reason)
    runtime.relay_target = created.value
    runtime.relay = runtime.relay_manager.connection_for_target(created.value)
    runtime.peer_addr = f"relay:{runtime.relay.identity}"
    return runtime


def shared_relay_root(test) -> str:
    """One folder for every client in a test.

    Two people using the same relay is the ordinary case, and a client never
    polls a topic it was not given, so sharing the folder shares nothing.
    """
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    return directory.name


def connect(host, guest, topic_uuid: str | None = None) -> dict:
    """Wire two runtimes the way the app does: the host decides to use its
    relay for the topic, composes an invitation, and the guest accepts it.

    Omit topic_uuid to share only the host's identity (the old `invite`);
    pass one to share a topic too (the old `share_board`). The identity
    travels either way - an invitation the invitee cannot attribute to anyone
    is not one. The first relay target is already the identity's home.
    """
    topic_uuids = [topic_uuid] if topic_uuid else []
    for uuid in topic_uuids:
        host.session.start_discussion(uuid)
        attached = host.mailbox_channel.attach_topics(
            [uuid], {"target_id": host.relay_target},
        )
        if not attached.ok:
            return {"status": "error", "reason": attached.reason}
    identity_uuid = host.session.identity.uuid
    invited_topics = topic_uuids or [identity_uuid]
    token = host.channel_manager.compose_token(invited_topics, {
        uuid: {
            "kind": "mailbox", "target_id": host.relay_target,
        }
        for uuid in {*invited_topics, identity_uuid}
    })
    if not token.ok:
        return {"status": "error", "reason": token.reason}
    result = guest.channel_manager.accept_token(token.value)
    if not result.ok:
        return {"status": "error", "reason": result.reason}
    sync(host, guest)
    return result.value


def sync(*runtimes, reconcile: bool = True) -> None:
    """Move work between clients the only way a relay can: each publishes
    what changed, then each reads what the others left.

    Twice, because a client given a topic in the first round has nothing of
    its own to publish until it has grafted it. Application reconciliation is
    the channel tick's after-apply phase, not a side effect of a later GET.
    """
    for _ in range(2):
        for runtime in runtimes:
            runtime.relay.write_presence()
            runtime.relay.publish_due_topics()
        for runtime in runtimes:
            runtime.relay.poll_and_apply()
            if reconcile:
                outcome = runtime.host.notify_peer_update()
                if outcome.effects:
                    runtime.deliver_effects(outcome.effects)
