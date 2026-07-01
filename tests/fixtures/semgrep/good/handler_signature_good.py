from constellation_node_sdk.transport.models import TransportPacket


async def handler(packet: TransportPacket) -> TransportPacket:
    return packet
