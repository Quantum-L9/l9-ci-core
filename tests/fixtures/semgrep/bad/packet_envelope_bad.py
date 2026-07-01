from legacy.transport import PacketEnvelope


def build(payload):
    return PacketEnvelope(payload=payload)
