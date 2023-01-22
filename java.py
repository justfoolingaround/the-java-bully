import asyncio
import os
import time
from collections import defaultdict, deque

import aiohttp
import dotenv

dotenv.load_dotenv()

TOKEN = os.getenv("TOKEN")

assert TOKEN, "TOKEN environment variable is not set."

CAPABILITIES = 1 << 9


DISCORD_WS_URL = "wss://gateway.discord.gg/"
DISCORD_VERSION = 9
DISCORD_API_URL = f"https://discord.com/api/v{DISCORD_VERSION}/"
CLIENT_PROPERTIES = {
    "os": "Windows",
    "browser": "Discord Client",
    "release_channel": "stable",
    "client_version": "1.0.9008",
    "os_version": "10.0.22621",
    "os_arch": "x64",
    "system_locale": "en-US",
}

WAIT_TIME = 300.0
JAVA_DEVELOPERS = [] # ["user_id, in string"]
MESSAGE = "https://cdn.discordapp.com/emojis/531182905851117588.png?size=96"


class TheJavaBully:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        token,
        message,
        wait_time=0.0,
        exempt_channels=None,
        targets=None,
        *,
        reply=False,
    ):
        self.session = session
        self.token = token
        self.wait_time = wait_time

        self.targets = targets or list()
        self.targets_channels = defaultdict(asyncio.Event)
        self.exempt_channels = exempt_channels or list()

        self.channel_message_store = defaultdict(deque)

        self.message = message
        self.reply = reply

    async def watch(self, channel_id):
        if channel_id in self.targets_channels or channel_id in self.exempt_channels:
            return

        event = self.targets_channels[channel_id]

        until = time.time() + self.wait_time

        while event.is_set() or time.time() < until:
            await asyncio.sleep(1)

        if event.is_set():
            return

        data = {
            "content": self.message,
        }

        if self.reply and self.channel_message_store[channel_id]:
            data["message_reference"] = {
                "message_id": self.channel_message_store[channel_id][0]["id"],
                "channel_id": channel_id,
            }

        await self.session.post(
            DISCORD_API_URL + f"channels/{channel_id}/messages",
            json=data,
            headers={"Authorization": self.token},
        )
        await self.unwatch(channel_id)

    async def unwatch(self, channel_id):
        self.targets_channels[channel_id].set()
        del self.targets_channels[channel_id]

    async def on_message_create(self, data: dict):

        channel_id = data["channel_id"]
        self.channel_message_store[channel_id].appendleft(data)

        author_id = data["author"]["id"]

        if author_id not in self.targets:
            if channel_id in self.targets_channels:
                await self.unwatch(channel_id)

            return

        return await self.watch(channel_id)

    async def on_message_delete(self, data: dict):
        channel_id = data["channel_id"]
        message_id = data["id"]

        messages_deque = self.channel_message_store[channel_id]

        for message in messages_deque.copy():
            if message["id"] == message_id:
                messages_deque.remove(message)

        if messages_deque and messages_deque[0]["author"]["id"] in self.targets:
            return await self.watch(channel_id)

        if channel_id in self.targets_channels:
            return await self.unwatch(channel_id)


async def setup_heartbeat(
    ws: aiohttp.ClientWebSocketResponse, interval, sequence: asyncio.LifoQueue
):
    while not ws.closed:
        data = await sequence.get()
        await ws.send_json({"op": 1, "d": data})
        await asyncio.sleep(interval / 1000)


async def ws_connect(
    loop: asyncio.AbstractEventLoop,
    session: aiohttp.ClientSession,
    token,
    message,
    wait_time=0.0,
    *,
    discord_ws_url=DISCORD_WS_URL,
    capabilities=CAPABILITIES,
    version=DISCORD_VERSION,
):
    bully = TheJavaBully(session, token, message, wait_time, targets=JAVA_DEVELOPERS)

    async with session.ws_connect(
        discord_ws_url,
        params={
            "encoding": "json",
            "v": version,
        },
        max_msg_size=0,
    ) as ws:

        is_bot = token.startswith("Bot ")

        await ws.send_json(
            {
                "op": 2,
                "d": {
                    "token": token,
                    "intents" if is_bot else "capabilities": capabilities,
                }
                | ({} if is_bot else {"properties": CLIENT_PROPERTIES}),
            }
        )

        data = await ws.receive_json()

        heartbeat_interval = data["d"]["heartbeat_interval"]
        sequence = asyncio.LifoQueue()

        loop.create_task(setup_heartbeat(ws, heartbeat_interval, sequence))

        async for msg in ws:
            data = msg.json()

            match data["t"]:

                case "MESSAGE_CREATE":
                    loop.create_task(bully.on_message_create(data["d"]))
                case "MESSAGE_DELETE":
                    loop.create_task(bully.on_message_delete(data["d"]))
                case "MESSAGE_BULK_DELETE":
                    for message in data["d"]["ids"]:
                        loop.create_task(
                            bully.on_message_delete(
                                {
                                    "id": message,
                                    "channel_id": data["d"]["channel_id"],
                                }
                            )
                        )

            await sequence.put(data["s"])


async def main(loop: asyncio.AbstractEventLoop = None):
    loop = loop or asyncio.get_event_loop()

    async with aiohttp.ClientSession() as session:
        await ws_connect(loop, session, TOKEN, MESSAGE, WAIT_TIME)


if __name__ == "__main__":
    asyncio.run(main())
