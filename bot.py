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
CONTRACT_ADDRESS = "0x9afF30868d56256bAD67a31d4e58f992d90f4E44"
CRONOS_RPC = "https://evm.cronos.org"
COLLECTION_NAME = "Monkey Muscle"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

SEEN_LOGS_FILE = "seen_logs.json"

# ====================== IGNORED ADDRESSES ======================
IGNORED_ADDRESSES = {
    "0x26d4a407355ffe98dcbcec6f03cfb14918802703",   # Staking Wallet
    # Add more addresses here if needed
}

# Lowercase all ignored addresses
IGNORED_ADDRESSES = {addr.lower() for addr in IGNORED_ADDRESSES}

intents = discord.Intents.default()
client = discord.Client(intents=intents)
w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(CRONOS_RPC))

contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=[{
    "inputs": [{"name": "tokenId", "type": "uint256"}],
    "name": "tokenURI",
    "outputs": [{"name": "", "type": "string"}],
    "stateMutability": "view",
    "type": "function"
}])

# ====================== PERSISTENT SEEN LOGS ======================
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

# ====================== KEEP-ALIVE ======================
app = Flask(__name__)
@app.route('/')
def home():
    return "✅ Monkey Muscle Sales Bot is running 24/7"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# ====================== HELPERS ======================
async def fetch_metadata(token_id):
    try:
        token_uri = await contract.functions.tokenURI(token_id).call()
        if token_uri.startswith("ipfs://"):
            token_uri = token_uri.replace("ipfs://", "https://ipfs.io/ipfs/")
        async with aiohttp.ClientSession() as session:
            async with session.get(token_uri) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    name = data.get("name", f"{COLLECTION_NAME} #{token_id}")
                    image = data.get("image") or data.get("image_url")
                    if image and image.startswith("ipfs://"):
                        image = image.replace("ipfs://", "https://ipfs.io/ipfs/")
                    attributes = data.get("attributes", [])
                    rarity_text = "\n".join([f"{a.get('trait_type', 'Trait')}: {a.get('value')}" for a in attributes]) if attributes else "No traits available"
                    return name, image, rarity_text
    except Exception as e:
        print(f"Metadata error #{token_id}: {e}")
    return f"{COLLECTION_NAME} #{token_id}", None, "Rarity unavailable"

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
    print(f"✅ Monkey Muscle Sales Bot is ONLINE as {client.user}")
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🧪 **Bot Restarted**\nStaking transfers ignored.")
    client.loop.create_task(sales_listener())

async def sales_listener():
    global seen_logs
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        print("❌ Channel not found!")
        return

    print("✅ Listening for REAL sales only...")

    while True:
        try:
            current_block = await w3.eth.block_number
            from_block = max(current_block - 15, 0)

            logs = await w3.eth.get_logs({
                'fromBlock': from_block,
                'toBlock': current_block,
                'address': CONTRACT_ADDRESS,
                'topics': [TRANSFER_TOPIC]
            })

            for log in logs:
                log_id = f"{log['blockNumber']}-{log['logIndex']}"
                if log_id in seen_logs:
                    continue

                if len(log["topics"]) != 4:
                    continue

                from_addr = "0x" + log["topics"][1].hex()[-40:].lower()
                to_addr = "0x" + log["topics"][2].hex()[-40:].lower()
                token_id = int(log["topics"][3].hex(), 16)

                # Skip mints
                if from_addr == "0x0000000000000000000000000000000000000000":
                    seen_logs.add(log_id)
                    continue

                # Skip staking transfers
                if from_addr in IGNORED_ADDRESSES or to_addr in IGNORED_ADDRESSES:
                    seen_logs.add(log_id)
                    save_seen_logs(seen_logs)
                    continue

                # Real sale
                seen_logs.add(log_id)
                save_seen_logs(seen_logs)

                name, image_url, rarity = await fetch_metadata(token_id)
                price_info = await get_sale_price(from_addr, to_addr, log["blockNumber"])

                embed = discord.Embed(
                    title="🛒 Monkey Muscle SOLD!",
                    description=f"**{name}**",
                    color=0x00ff88,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Token ID", value=f"#{token_id}", inline=True)
                embed.add_field(name="Buyer", value=f"`{to_addr[:8]}...`", inline=True)
                embed.add_field(name="Seller", value=f"`{from_addr[:8]}...`", inline=True)
                embed.add_field(name="Price", value=price_info, inline=False)
                embed.add_field(name="Rarity / Traits", value=rarity[:600] + ("..." if len(rarity) > 600 else ""), inline=False)
                embed.add_field(name="Contract", value=f"`{CONTRACT_ADDRESS}`", inline=False)
                embed.set_footer(text="DISCORD SALES BOT BY MONKEY MUSCLE")

                if image_url:
                    embed.set_image(url=image_url)

                await channel.send(embed=embed)
                print(f"✅ Posted REAL sale → Token #{token_id} | {price_info}")

            await asyncio.sleep(10)

        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(15)

# ====================== START ======================
if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    client.run(DISCORD_TOKEN)
