"""Side-channel sender: topic metadata for topic-bound routes."""

from hermes_internal_bridge.plugin import GatewayMessageSender


def test_thread_metadata_passed_for_topic_address():
    assert GatewayMessageSender._thread_metadata({"thread_id": "t-9"}) == {"thread_id": "t-9"}


def test_thread_metadata_absent_without_topic():
    assert GatewayMessageSender._thread_metadata({"thread_id": ""}) is None
