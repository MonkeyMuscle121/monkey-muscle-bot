import discord
from discord import app_commands
import asyncio
import os
import json
from datetime import datetime
from urllib.parse import quote
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
        "ignored": {"0x26d4a407355ffe98dcbcec6f03cfb14918802703"},
        "post_mints": False,
        "ebisu_slug": "monkey-muscle"
    },
    {
        "address": "0xEe553dcB65502f69F33AD1Ece015740dA9Cf0f02",
        "name": "Monkey Muscle VIP",
        "color": 0xff0000,
        "ignored": set(),
        "post_mints": True,
        "ebisu_slug": "monkey-muscle-v-i-p"
    }
]

for col in COLLECTIONS:
    col["ignored"] = {addr.lower() for addr in col["ignored"]}

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
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
    try:
        with open(SEEN_LOGS_FILE, "w") as f:
            json.dump(list(seen), f)
    except Exception as e:
        print(f"Failed to save seen_logs: {e}")

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

        urls_to_try = []
        if token_uri.startswith("ipfs://"):
            cid = token_uri.replace("ipfs://", "")
            urls_to_try = [
                f"https://gateway.pinata.cloud/ipfs/{cid}",
                f"https://cloudflare-ipfs.com/ipfs/{cid}",
                f"https://dweb.link/ipfs/{cid}",
                f"https://ipfs.io/ipfs/{cid}",
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
                            break
            except:
                continue

        if not data:
            return f"NFT #{token_id}", None, "Rarity unavailable"

        name = data.get("name") or data.get("title") or f"NFT #{token_id}"

        image = (
            data.get("image")
            or data.get("image_url")
            or data.get("imageUrl")
            or data.get("image_uri")
            or data.get("imageURI")
            or data.get("animation_url")
            or (data.get("properties") or {}).get("image")
            or (data.get("properties") or {}).get("image_url")
        )

        if isinstance(image, dict):
            image = image.get("url") or image.get("ipfs") or image.get("gateway")

        if image and isinstance(image, str) and image.startswith("ipfs://"):
            cid = image.replace("ipfs://", "")
            image = f"https://gateway.pinata.cloud/ipfs/{cid}"

        attributes = data.get("attributes") or data.get("traits") or []
        rarity_text = "\n".join([
            f"{a.get('trait_type', a.get('traitType', 'Trait'))}: {a.get('value')}"
            for a in attributes if isinstance(a, dict)
        ]) if attributes else "No traits available"

        return name, image, rarity_text

    except Exception as e:
        print(f"Metadata error #{token_id}: {e}")
    return f"NFT #{token_id}", None, "Rarity unavailable"

async def get_sale_price(from_addr, to_addr, block_number):
    try:
        for i in range(-3, 8):
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
                    if from_addr.lower() in (tx_from, tx_to) or to_addr.lower() in (tx_from, tx_to):
                        price_cro = tx["value"] / 10**18
                        if price_cro > 0.01:
                            return f"{price_cro:.2f} CRO"
        return None
    except:
        return None

def make_share_view(collection_name, token_id, price_text, ebisu_slug):
    marketplace_url = f"https://app.ebisusbay.com/collection/cronos/{ebisu_slug}/{token_id}"
    text = f"Just got {collection_name} #{token_id}! {price_text} 🦍\n{marketplace_url}"
    share_url = f"https://twitter.com/intent/tweet?text={quote(text)}"
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Share on X", url=share_url, style=discord.ButtonStyle.link))
    return view

@tree.command(name="testog", description="Post a real sample sale of the original Monkey Muscle collection")
async def testog(interaction: discord.Interaction):
    await interaction.response.defer()
    col = COLLECTIONS[0]
    token_id = 787
    name, image_url, rarity = await fetch_metadata(col["address"], token_id)
    embed = discord.Embed(title=f"🛒 {col['name']} SOLD! (TEST)", description=f"**{name}**", color=col["color"], timestamp=datetime.utcnow())
    embed.add_field(name="Token ID", value=f"#{token_id}", inline=True)
    embed.add_field(name="Buyer", value="`0xTestB...`", inline=True)
    embed.add_field(name="Seller", value="`0xTestS...`", inline=True)
    embed.add_field(name="Price", value="150.00 CRO", inline=False)
    embed.add_field(name="Rarity / Traits", value=rarity[:600], inline=False)
    embed.add_field(name="Contract", value=f"`{col['address']}`", inline=False)
    embed.set_footer(text="DISCORD SALES BOT BY MONKEY MUSCLE | TEST")
    if image_url:
        embed.set_image(url=image_url)
    view = make_share_view(col["name"], token_id, "150.00 CRO", col["ebisu_slug"])
    await interaction.followup.send(embed=embed, view=view)

@tree.command(name="testvip", description="Post a real sample sale of the VIP collection")
async def testvip(interaction: discord.Interaction):
    await interaction.response.defer()
    col = COLLECTIONS[1]
    token_id = 256
    name, image_url, rarity = await fetch_metadata(col["address"], token_id)
    embed = discord.Embed(title=f"🛒 {col['name']} SOLD! (TEST)", description=f"**{name}**", color=col["color"], timestamp=datetime.utcnow())
    embed.add_field(name="Token ID", value=f"#{token_id}", inline=True)
    embed.add_field(name="Buyer", value="`0xTestB...`", inline=True)
    embed.add_field(name="Seller", value="`0xTestS...`", inline=True)
    embed.add_field(name="Price", value="99.99 CRO", inline=False)
    embed.add_field(name="Rarity / Traits", value=rarity[:600], inline=False)
    embed.add_field(name="Contract", value=f"`{col['address']}`", inline=False)
    embed.set_footer(text="DISCORD SALES BOT BY MONKEY MUSCLE | TEST")
    if image_url:
        embed.set_image(url=image_url)
    view = make_share_view(col["name"], token_id, "99.99 CRO", col["ebisu_slug"])
    await interaction.followup.send(embed=embed, view=view)

@tree.command(name="testmint", description="Post a real sample mint of the VIP collection")
async def testmint(interaction: discord.Interaction):
    await interaction.response.defer()
    col = COLLECTIONS[1]
    token_id = 512
    name, image_url, rarity = await fetch_metadata(col["address"], token_id)
    embed = discord.Embed(title=f"🆕 {col['name']} MINTED! (TEST)", description=f"**{name}**", color=col["color"], timestamp=datetime.utcnow())
    embed.add_field(name="Token ID", value=f"#{token_id}", inline=True)
    embed.add_field(name="Minter", value="`0xTestM...`", inline=True)
    embed.add_field(name="Price", value="Mint", inline=False)
    embed.add_field(name="Rarity / Traits", value=rarity[:600], inline=False)
    embed.add_field(name="Contract", value=f"`{col['address']}`", inline=False)
    embed.set_footer(text="DISCORD SALES BOT BY MONKEY MUSCLE | TEST")
    if image_url:
        embed.set_image(url=image_url)
    view = make_share_view(col["name"], token_id, "Mint", col["ebisu_slug"])
    await interaction.followup.send(embed=embed, view=view)

@client.event
async def on_ready():
    print(f"✅ Bot ONLINE as {client.user}")
    await tree.sync()
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🧪 **Bot Restarted**\nVIP mints + real sales only. Commands: `/testog` `/testvip` `/testmint`")
    client.loop.create_task(sales_listener())

async def sales_listener():
    global seen_logs
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        print("❌ Channel not found!")
        return

    print("✅ Listening for VIP mints + real sales...")

    while True:
        try:
            current_block = await w3.eth.block_number
            from_block = max(current_block - 20, 0)  # wider window

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

                    is_mint = from_addr == "0x0000000000000000000000000000000000000000"

                    if is_mint:
                        if not col.get("post_mints", False):
                            seen_logs.add(log_id)
                            save_seen_logs(seen_logs)
                            continue
                        print(f"🆕 MINT detected: {col['name']} #{token_id}")
                    else:
                        if from_addr in col["ignored"] or to_addr in col["ignored"]:
                            seen_logs.add(log_id)
                            save_seen_logs(seen_logs)
                            continue
                        price_info = await get_sale_price(from_addr, to_addr, log["blockNumber"])
                        if not price_info:
                            seen_logs.add(log_id)
                            save_seen_logs(seen_logs)
                            continue
                        print(f"💰 SALE detected: {col['name']} #{token_id} @ {price_info}")

                    # Post it
                    seen_logs.add(log_id)
                    save_seen_logs(seen_logs)

                    name, image_url, rarity = await fetch_metadata(col["address"], token_id)

                    if is_mint:
                        embed = discord.Embed(title=f"🆕 {col['name']} MINTED!", description=f"**{name}**", color=col["color"], timestamp=datetime.utcnow())
                        embed.add_field(name="Token ID", value=f"#{token_id}", inline=True)
                        embed.add_field(name="Minter", value=f"`{to_addr[:8]}...`", inline=True)
                        embed.add_field(name="Price", value="Mint", inline=False)
                        price_text = "Mint"
                    else:
                        embed = discord.Embed(title=f"🛒 {col['name']} SOLD!", description=f"**{name}**", color=col["color"], timestamp=datetime.utcnow())
                        embed.add_field(name="Token ID", value=f"#{token_id}", inline=True)
                        embed.add_field(name="Buyer", value=f"`{to_addr[:8]}...`", inline=True)
                        embed.add_field(name="Seller", value=f"`{from_addr[:8]}...`", inline=True)
                        embed.add_field(name="Price", value=price_info, inline=False)
                        price_text = price_info

                    embed.add_field(name="Rarity / Traits", value=rarity[:600], inline=False)
                    embed.add_field(name="Contract", value=f"`{col['address']}`", inline=False)
                    embed.set_footer(text="DISCORD SALES BOT BY MONKEY MUSCLE")
                    if image_url:
                        embed.set_image(url=image_url)

                    view = make_share_view(col["name"], token_id, price_text, col["ebisu_slug"])
                    await channel.send(embed=embed, view=view)
                    print(f"✅ Posted {col['name']} {'MINT' if is_mint else 'SALE'} #{token_id}")

            await asyncio.sleep(10)

        except Exception as e:
            print(f"Listener error: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    client.run(DISCORD_TOKEN)
