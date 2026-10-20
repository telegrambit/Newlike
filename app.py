from flask import Flask, request, jsonify
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson
import binascii
import aiohttp
import requests
import json
import os
import FlashLikeCount_pb2
import random

app = Flask(__name__)

def _varint(n):
    out = b""
    while n > 0x7F:
        out += bytes([(n & 0x7F) | 0x80])
        n >>= 7
    return out + bytes([n])

def build_like_message(uid, region):
    uid = int(uid)
    rb = region.encode("utf-8")
    out = b""
    out += bytes([0x08]) + _varint(uid)
    out += bytes([0x12]) + _varint(len(rb)) + rb
    return out

def build_uid_generator_message(uid):
    uid = int(uid)
    out = b""
    out += bytes([0x08]) + _varint(uid)
    out += bytes([0x10]) + _varint(1)
    return out

def encrypt_message(plaintext):
    key = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return binascii.hexlify(cipher.encrypt(pad(plaintext, AES.block_size))).decode('utf-8')
    
def load_tokens(server_name):
    try:
        if server_name == "IND":
            filename = "token_ind.json"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            filename = "token_br.json"
        else:
            filename = "token_bd.json"

        with open(filename, "r") as f:
            data = json.load(f)
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and 'token' in data[0]:
                return data
            elif isinstance(data, list) and isinstance(data[0], str):
                return [{"token": t} for t in data]
            return []
    except FileNotFoundError:
        return []
    except Exception:
        return []

def get_base_url(server_name):
    if server_name == "IND":
        return "https://client.ind.freefiremobile.com"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com"
    else:
        return "https://clientbp.ggpolarbear.com"
        
async def send_request(encrypted_uid, token, url, semaphore):
    async with semaphore:
        edata = bytes.fromhex(encrypted_uid)
        headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
            'Authorization': f"Bearer {token}",
            'Content-Type': "application/x-www-form-urlencoded",
            'X-GA': "v1 1",
            'ReleaseVersion': "OB55"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=edata, headers=headers, timeout=5) as response:
                    return response.status
        except:
            return 500

async def send_multiple_requests(uid, server_name):
    protobuf_message = build_like_message(uid, server_name)
    encrypted_uid = encrypt_message(protobuf_message)

    tokens = load_tokens(server_name)
    if not tokens:
        return []

    token_list = [t.get("token") for t in tokens if t.get("token")]
    if not token_list:
        return []

    random.shuffle(token_list)
    url = f"{get_base_url(server_name)}/LikeProfile"

    semaphore = asyncio.Semaphore(50)
    tasks = [send_request(encrypted_uid, token, url, semaphore) for token in token_list]
    return await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

def enc(uid):
    return encrypt_message(build_uid_generator_message(uid))

def decode_protobuf(binary):
    try:
        items = FlashLikeCount_pb2.Info()
        items.ParseFromString(binary)
        return items
    except Exception:
        return None

def make_request(encrypt, server_name, token):
    url = f"{get_base_url(server_name)}/GetPlayerPersonalShow"

    edata = bytes.fromhex(encrypt)
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'X-GA': "v1 1",
        'ReleaseVersion': "OB55"
    }

    try:
        response = requests.post(url, data=edata, headers=headers, verify=False, timeout=10)
        return decode_protobuf(response.content)
    except:
        return None

@app.route('/like', methods=['GET'])
def handle_requests():
    uid = request.args.get("uid")
    server_name = request.args.get("server_name", "").upper()

    if not uid or not server_name:
        return jsonify({"error": "UID and server_name are required"}), 400

    data_tokens = load_tokens(server_name)
    if not data_tokens:
        return jsonify({"error": "No tokens found for this server"}), 500

    token = data_tokens[0]['token']
    encrypt = enc(uid)

    before = make_request(encrypt, server_name, token)
    if before is None:
        return jsonify({"error": "Server error or Invalid Token/UID", "status": 0}), 200

    try:
        data_before = json.loads(MessageToJson(before))
        before_like = int(data_before.get('AccountInfo', {}).get('Likes', 0))
    except Exception:
        return jsonify({"error": "Data parsing failed", "status": 0}), 200

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_multiple_requests(uid, server_name))
        loop.close()
    except Exception:
        pass

    after = make_request(encrypt, server_name, token)
    if after is None:
        return jsonify({"error": "Could not verify likes after command", "status": 0}), 200

    try:
        data_after = json.loads(MessageToJson(after))
        account_info = data_after.get('AccountInfo', {})

        after_like = int(account_info.get('Likes', 0))
        player_id = int(account_info.get('UID', 0))
        name = str(account_info.get('PlayerNickname', ''))
        level = int(account_info.get('Levels', 0))
        region = str(account_info.get('PlayerRegion', ''))
        if level == 0 and hasattr(after, 'AccountInfo') and hasattr(after.AccountInfo, 'Levels'):
            level = int(after.AccountInfo.Levels)
        like_given = after_like - before_like
        status = 1 if like_given != 0 else 2
        return jsonify({
            "LikesGivenByAPI": like_given,
            "LikesafterCommand": after_like,
            "LikesbeforeCommand": before_like,
            "PlayerNickname": name,
            "UID": player_id,
            "Level": level,
            "Region": region,
            "status": status,
            "Credits": "t.me/FL4SH_FF"
        })
    except Exception as e:
        return jsonify({"error": str(e), "status": 0}), 500
        
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 3535))
    app.run(host="0.0.0.0", port=port)