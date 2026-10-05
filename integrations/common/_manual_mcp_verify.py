import asyncio
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def check(url, label):
    print(f"=== {label}: {url} ===")
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"{label} tools ({len(names)}):", names)
            return names

async def main():
    core_tools = await check("http://mcp-core:8000/mcp", "mcp-core")
    payment_tools = await check("http://mcp-payment:8000/mcp", "mcp-payment")

    print("\n=== calling get_subscription_status for NH-100001 on mcp-core ===")
    async with streamable_http_client("http://mcp-core:8000/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("find_customer", {"customer_no": "NH-100001"})
            print("find_customer ->", json.dumps(result.structured_content, indent=2, ensure_ascii=False))

            result2 = await session.call_tool("get_subscription_status", {"customer_no": "NH-100001"})
            print("get_subscription_status ->", json.dumps(result2.structured_content, indent=2, ensure_ascii=False))

            result3 = await session.call_tool("request_refund", {"payment_id": 1, "amount_try": 10.0, "reason": "demo"})
            print("request_refund (expect SCOPE_DENIED) ->", json.dumps(result3.structured_content, indent=2, ensure_ascii=False))

    print("\n=== calling detect_duplicate_charges on mcp-payment ===")
    async with streamable_http_client("http://mcp-payment:8000/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result4 = await session.call_tool("get_gateway_health", {})
            print("get_gateway_health ->", json.dumps(result4.structured_content, indent=2, ensure_ascii=False))

asyncio.run(main())
