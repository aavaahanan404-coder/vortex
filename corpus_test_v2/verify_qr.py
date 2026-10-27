#!/usr/bin/env python3
"""Reference Tier-1 check: decode QR from a PDF page and verify the mock HMAC against world.sqlite.
Usage: python verify_qr.py documents/SYN-I0001/04_fire_safety_certificate.pdf"""
import sys, subprocess, sqlite3, hmac, hashlib, cv2
pdf = sys.argv[1]; subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", pdf, "/tmp/_qr"], check=True)
data, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread("/tmp/_qr.png"))
if not data: sys.exit("no QR found")
_, issuer, cert, inst, valid, sig = data.split("|")
key = dict(sqlite3.connect("world/world.sqlite").execute("select issuer, secret_hex from issuer_keys"))[issuer]
ok = hmac.new(bytes.fromhex(key), f"{cert}|{inst}|{valid}".encode(), hashlib.sha256).hexdigest()[:20] == sig
print({"payload": data, "signature_valid": ok})
