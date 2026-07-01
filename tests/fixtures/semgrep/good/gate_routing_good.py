from constellation_node_sdk import GateClient

async def forward(packet):
    client = GateClient()
    return await client.send_to_gate(packet)
