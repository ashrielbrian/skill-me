"""Tests for the event bus."""
import asyncio
import pytest
from eventbus import EventBus, Event


@pytest.mark.asyncio
async def test_publish_subscribe():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event)

    bus.subscribe("test.topic", handler)
    await bus.start()
    await bus.publish(Event(topic="test.topic", payload={"key": "value"}))
    await asyncio.sleep(0.2)
    await bus.stop()

    assert len(received) == 1
    assert received[0].payload["key"] == "value"


@pytest.mark.asyncio
async def test_wildcard_subscribe():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event)

    bus.subscribe("orders.*", handler)
    await bus.start()
    await bus.publish(Event(topic="orders.created", payload={}))
    await bus.publish(Event(topic="orders.updated", payload={}))
    await bus.publish(Event(topic="users.created", payload={}))
    await asyncio.sleep(0.2)
    await bus.stop()

    assert len(received) == 2


@pytest.mark.asyncio
async def test_dead_letter():
    bus = EventBus()
    await bus.start()
    await bus.publish(Event(topic="unhandled.topic", payload={}))
    await asyncio.sleep(0.2)
    await bus.stop()

    assert bus.status()["dead_letters"] == 1
