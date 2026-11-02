"""Real SDK signing with the existing merchant verifier, transported offline."""
import json
import os
import sys

from eth_account import Account
from singit import SingIt, SingItPaymentError
import singit.client as client
from helpers.merchant import exact_offer, encode, decode, verify_exact_payload

account = Account.from_key(os.environ["TEST_EVM_KEY"])
offer = exact_offer(network="eip155:84532", asset="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                    pay_to="0x" + "1" * 40, amount="2000")
paid = []


def post(url, body, headers):
    if not headers:
        return 402, {"payment-required": encode({"x402Version": 2, "resource": {"url": url}, "accepts": [offer]})}, b"{}"
    payload = decode(headers["PAYMENT-SIGNATURE"])
    ok, payer = verify_exact_payload(payload)
    assert ok
    paid.append(True)
    return 200, {"payment-response": encode({"network": offer["network"], "payer": payer, "transaction": "0x" + "ab" * 32})}, json.dumps({"billing": {"totalAtomic": "2000"}}).encode()


if len(sys.argv) == 1:
    client._post_json = post
sdk = SingIt(evm_account=account, network="base-sepolia", pay_to=offer["payTo"],
             max_per_call_usd=.003, max_per_day_usd=.003,
             base_url=sys.argv[1] if len(sys.argv) > 1 else "https://offline.invalid")
try:
    sdk.chat(messages=[{"role": "user", "content": "test"}], scheme="exact")
    print(json.dumps({"ok": True, "paid": len(paid)}))
except SingItPaymentError as error:
    print(json.dumps({"ok": False, "paid": len(paid), "error": str(error)}))
