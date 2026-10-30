import discord
import asyncio
import os
import json
from datetime import datetime
from web3 import AsyncWeb3
from dotenv import load_dotenv
import aiohttp
from flask import Flask
from threading import Thread

load_dotenv()

# ========================= CONFIG =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1344121702526746685
CRONOS_RPC = "https://evm.cronos.org"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SEEN_LOGS_FILE = "seen_logs.json"

COLLECTIONS = [
    {
        "address": "0x9afF30868d56256bAD67a31d4e58f992d90f4E44",
        "name": "Monkey Muscle",
        "color": 0x00ff88,
        "ignored": {"0x26d4a407355ffe98dcbcec6f03cfb14918802703"}
    },
    {
        "address": "0xEe553dcB65502f69F33AD1Ece015740dA9Cf0f02",
        "name": "Monkey Muscle VIP",
        "color": 0xff0000,
        "ignored": set()
    }
]

for col in COLLECTIONS:
    col["ignored"] = {addr.lower() for addr in col["ignored"]}

intents = discord.Intents.default()
client = discord.Client(intents=intents)
w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(CRONOS_RPC))

contracts = {}
for col in COLLECTIONS:
    contracts[col["address"].lower()] = w3.eth.contract(
        address=col["address"],
        abi=[{
            "inputs": [{"name": "tokenId", "type": "uint256"}],
            "name": "tokenURI",
            "outputs": [{"name": "", "type": "string"}],
            "stateMutability": "view",
            "type": "function"
        }]
    )

def load_seen_logs():
    if os.path.exists(SEEN_LOGS_FILE):
        try:
            with open(SEEN_LOGS_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_seen_logs(seen):
    with open(SEEN_LOGS_FILE, "w") as f:
        json.dump(list(seen), f)

seen_logs = load_seen_logs()

app = Flask(__name__)
@app.route('/')
def home():
    return "✅ Multi-Collection Sales Bot is running 24/7"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# ====================== HELPERS ======================
async def fetch_metadata(contract_address, token_id):
    try:
        contract = contracts[contract_address.lower()]
        token_uri = await contract.functions.tokenURI(token_id).call()
        print(f"Token URI for {contract_address} #{token_id}: {token_uri}")

        # Build list of URLs to try
        urls_to_try = []
        if token_uri.startswith("ipfs://"):
            cid = token_uri.replace("ipfs://", "")
            urls_to_try = [
                f"https://ipfs.io/ipfs/{cid}",
                f"https://gateway.pinata.cloud/ipfs/{cid}",
                f"https://cloudflare-ipfs.com/ipfs/{cid}",
                f"https://dweb.link/ipfs/{cid}",
            ]
        elif token_uri.startswith("http"):
            urls_to_try = [token_uri]
        else:
            urls_to_try = [token_uri]

        data = None
        for url in urls_to_try:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=12) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            print(f"✅ Metadata loaded from {url}")
                            break
            except Exception as e:
                print(f"Failed {url}: {e}")
                continue

        if not data:
            print(f"❌ All gateways failed for #{token_id}")
            return f"NFT #{token_id}", None, "Rarity unavailable"

        name = data.get("name") or data.get("title") or f"NFT #{token_id}"

        # Broader image search
        image = (
            data.get("image")
            or data.get("image_url")
            or data.get("imageUrl")
            or data.get("image_uri")
            or (data.get("properties") or {}).get("image")
            or (data.get("properties") or {}).get("image_url")
        )

        if isinstance(image, dict):
            image = image.get("url") or image.get("ipfs") or image.get("gateway")

        if image and isinstance(image, str):
            if image.startswith("ipfs://"):
                cid = image.replace("ipfs://", "")
                image = f"https://ipfs.io/ipfs/{cid}"

        attributes = data.get("attributes") or data.get("traits") or []
        rarity_text = "\n".join([
            f"{a.get('trait_type', a.get('traitType', 'Trait'))}: {a.get('value')}"
            for a in attributes if isinstance(a, dict)
        ]) if attributes else "No traits available"

        return name, image, rarity_text

    except Exception as e:
        print(f"Metadata error #{token_id} on {contract_address}: {e}")
    return f"NFT #{token_id}", None, "Rarity unavailable"

async def get_sale_price(from_addr, to_addr, block_number):
    try:
        for i in range(-2, 6):
            check_block = block_number + i
            if check_block < 0:
                continue
            block = await w3.eth.get_block(check_block, full_transactions=True)
            if not block or not block.get("transactions"):
                continue
            for tx in block["transactions"]:
                if tx.get("value", 0) > 0:
                    tx_from = (tx.get("from") or "").lower()
                    tx_to = (tx.get("to") or "").lower()
                    if (from_addr.lower() in (tx_from, tx_to) or to_addr.lower() in (tx_from, tx_to)):
                        price_cro = tx["value"] / 10**18
                        return f"{price_cro:.2f} CRO"
        return "Sold on Ebisu's Bay"
    except:
        return "Sold on Ebisu's Bay"

@client.event
async def on_ready():
    print(f"✅ Multi-Collection Sales Bot is ONLINE as {client.user}")
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🧪 **Bot Restarted**\nNow monitoring Monkey Muscle (green) and Monkey Muscle VIP (red).")
        await send_sample_embeds(channel)
    client.loop.create_task(sales_listener())

async def send_sample_embeds(channel):
    samples = [
        {
            "col": COLLECTIONS[0],
            "token_id": 100,
            "price": "123.45 CRO",
            "buyer": "0xSampleB...",
            "seller": "0xSampleS...",
            "fallback_image": "https://via.placeholder.com/400x400/00ff88/000000?text=Monkey+Muscle+Sample"
        },
        {
            "col": COLLECTIONS[1],
            "token_id": 256,
            "price": "99.99 CRO",
            "buyer": "0xSampleB...",
            "seller": "0xSampleS...",
        },
    ]
    for sample in samples:
        col = sample["col"]
        token_id = sample["token_id"]
        try:
            name, image_url, rarity = await fetch_metadata(col["address"], token_id)
        except Exception as e:
            print(f"Sample metadata failed for {col['name']} #{token_id}: {e}")
            name, image_url, rarity = f"{col['name']} #{token_id}", None, "Sample traits"

        if not image_url and sample.get("fallback_image"):
            image_url = sample["fallback_image"]

        embed = discord.Embed(
            title=f"🛒 {col['name']} SOLD! (SAMPLE)",
            description=f"**{name}**\n*This is a ghost sample for UI check only*",
            color=col["color"],
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Token ID", value=f"#{token_id}", inline=True)
        embed.add_field(name="Buyer", value=f"`{sample['buyer']}`", inline=True)
        embed.add_field(name="Seller", value=f"`{sample['seller']}`", inline=True)
        embed.add_field(name="Price", value=sample["price"], inline=False)
        embed.add_field(name="Rarity / Traits", value=rarity[:600] + ("..." if len(rarity) > 600 else ""), inline=False)
        embed.add_field(name="Contract", value=f"`{col['address']}`", inline=False)
        embed.set_footer(text="DISCORD SALES BOT BY MONKEY MUSCLE | SAMPLE")

        if image_url:
            embed.set_image(url=image_url)

        await channel.send(embed=embed)
        print(f"✅ Sent sample embed for {col['name']}")
        await asyncio.sleep(1)

async def sales_listener():
    global seen_logs
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        print("❌ Channel not found!")
        return

    print("✅ Listening for REAL sales on both collections...")

    while True:
        try:
            current_block = await w3.eth.block_number
            from_block = max(current_block - 15, 0)

            for col in COLLECTIONS:
                logs = await w3.eth.get_logs({
                    'fromBlock': from_block,
                    'toBlock': current_block,
                    'address': col["address"],
                    'topics': [TRANSFER_TOPIC]
                })

                for log in logs:
                    log_id = f"{col['address'].lower()}-{log['blockNumber']}-{log['logIndex']}"
                    if log_id in seen_logs:
                        continue

                    if len(log["topics"]) != 4:
                        continue

                    from_addr = "0x" + log["topics"][1].hex()[-40:].lower()
                    to_addr = "0x" + log["topics"][2].hex()[-40:].lower()
                    token_id = int(log["topics"][3].hex(), 16)

                    if from_addr == "0x0000000000000000000000000000000000000000":
                        seen_logs.add(log_id)
                        continue

                    if from_addr in col["ignored"] or to_addr in col["ignored"]:
                        seen_logs.add(log_id)
                        save_seen_logs(seen_logs)
                        continue

                    seen_logs.add(log_id)
                    save_seen_logs(seen_logs)

                    name, image_url, rarity = await fetch_metadata(col["address"], token_id)
                    price_info = await get_sale_price(from_addr, to_addr, log["blockNumber"])

                    embed = discord.Embed(
                        title=f"🛒 {col['name']} SOLD!",
                        description=f"**{name}**",
                        color=col["color"],
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(name="Token ID", value=f"#{token_id}", inline=True)
                    embed.add_field(name="Buyer", value=f"`{to_addr[:8]}...`", inline=True)
                    embed.add_field(name="Seller", value=f"`{from_addr[:8]}...`", inline=True)
                    embed.add_field(name="Price", value=price_info, inline=False)
                    embed.add_field(name="Rarity / Traits", value=rarity[:600] + ("..." if len(rarity) > 600 else ""), inline=False)
                    embed.add_field(name="Contract", value=f"`{col['address']}`", inline=False)
                    embed.set_footer(text="DISCORD SALES BOT BY MONKEY MUSCLE")

                    if image_url:
                        embed.set_image(url=image_url)

                    await channel.send(embed=embed)
                    print(f"✅ Posted {col['name']} sale → Token #{token_id} | {price_info}")

            await asyncio.sleep(10)

        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    client.run(DISCORD_TOKEN)
