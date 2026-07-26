#!/usr/bin/env python3
"""
Rabithole local server — SQLite persistence + auth.

Usage:
    python3 server.py
    Open http://localhost:8080

API:
    POST /api/login              {username, password}
    POST /api/logout             (Authorization: Bearer <token>)
    GET  /api/me                 (Authorization: Bearer <token>)
    GET/POST /api/products
    GET/PUT/DELETE /api/products/<id>
    GET/POST /api/posts
    GET/PUT/DELETE /api/posts/<id>
    GET  /api/paypal/config              public PayPal client id / currency
    POST /api/paypal/orders               {items: [{productId, quantity}]}  -> create PayPal order for a cart
    POST /api/paypal/orders/<id>/capture  -> capture an approved order
    GET  /api/orders                      (Authorization: Bearer <token>) owner order history
    PUT  /api/orders/<id>                  (Authorization: Bearer <token>) update fulfillment status
    DELETE /api/orders/<id>                (Authorization: Bearer <token>) delete an order record
    POST /api/upload                      (Authorization: Bearer <token>) upload a product image
                                           (raw image bytes as the body, Content-Type: image/png|jpeg|webp|gif)

Default owner account (seeded in DB):
    username: admin
    password: rabithole

PayPal setup:
    Edit paypal_config.json with your Client ID and Secret from
    https://developer.paypal.com/dashboard/applications (use a Sandbox app
    to test with no real money first). Leave mode as "sandbox" until you're
    ready to accept real payments, then switch to "live" with live keys.
"""

import base64
import hashlib
import json
import posixpath
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "db" / "rabithole.db"
PAYPAL_CONFIG_PATH = ROOT / "paypal_config.json"
UPLOADS_DIR = ROOT / "assets" / "uploads"
PORT = 8080
SESSION_DAYS = 7
FULFILLMENT_STATUSES = {"Pending", "Shipped", "Delivered", "Cancelled"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB
UPLOAD_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

# Everything the static file handler is allowed to serve. Anything else under
# ROOT (server.py, manage_users.py, paypal_config.json, db/, .claude/, ...) is
# server-side-only and must never be reachable by a plain GET.
ALLOWED_STATIC_FILES = {
    "/",
    "/index.html",
    "/products.html",
    "/blog.html",
    "/login.html",
    "/admin.html",
    "/orders.html",
    "/account.html",
    "/add-product.html",
    "/create-post.html",
    "/manage-products.html",
    "/manage-posts.html",
}
ALLOWED_STATIC_PREFIXES = ("/assets/",)


def is_allowed_static_path(path):
    normalized = posixpath.normpath(path)
    if normalized in ALLOWED_STATIC_FILES:
        return True
    return any(normalized.startswith(prefix) for prefix in ALLOWED_STATIC_PREFIXES)

# Cart snapshot for each PayPal order awaiting capture: paypal_order_id -> list
# of {"productId", "productName", "quantity", "unitPrice"}. In-memory only —
# this is a local dev server, and carts are short-lived between create and
# capture (typically seconds).
PENDING_CARTS = {}
PENDING_CARTS_LOCK = threading.Lock()

PAYPAL_API_BASE = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}


def load_paypal_config():
    defaults = {"mode": "sandbox", "client_id": "", "secret": "", "currency": "USD"}
    try:
        with open(PAYPAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            defaults.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return defaults


def paypal_get_token(config):
    base = PAYPAL_API_BASE[config["mode"]]
    auth = base64.b64encode(f"{config['client_id']}:{config['secret']}".encode()).decode()
    req = urllib.request.Request(
        base + "/v1/oauth2/token",
        data=b"grant_type=client_credentials",
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())["access_token"]


def paypal_api(method, path, config, token, body=None):
    base = PAYPAL_API_BASE[config["mode"]]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else {}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str, salt: str):
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000
    )
    return dk.hex()


def row_to_product(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "price": row["price"],
        "category": row["category"],
        "status": row["status"],
        "description": row["description"],
        "image": row["image"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_post(row):
    tags = row["tags"]
    try:
        tags = json.loads(tags) if tags else []
    except Exception:
        tags = []
    return {
        "id": row["id"],
        "title": row["title"],
        "tags": tags,
        "content": row["content"],
        "date": row["date"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "displayName": row["display_name"] or row["username"],
    }


def row_to_order(row):
    return {
        "id": row["id"],
        "productId": row["product_id"],
        "productName": row["product_name"],
        "amount": row["amount"],
        "currency": row["currency"],
        "payerEmail": row["payer_email"],
        "payerName": row["payer_name"],
        "paypalOrderId": row["paypal_order_id"],
        "status": row["status"],
        "fulfillmentStatus": row["fulfillment_status"],
        "createdAt": row["created_at"],
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        # Local dev server — never let the browser cache stale HTML/JS/API responses.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization"
        )
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _bearer_token(self):
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def _current_user(self):
        """Return user row if valid session token, else None."""
        token = self._bearer_token()
        if not token:
            return None
        now = int(time.time() * 1000)
        conn = get_db()
        row = conn.execute(
            """
            SELECT u.* FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, now),
        ).fetchone()
        conn.close()
        return row

    def _require_auth(self):
        user = self._current_user()
        if not user:
            self._send_json({"error": "Unauthorized"}, 401)
            return None
        return user

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization"
        )
        self.end_headers()

    # ---------- GET ----------
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/me":
            user = self._current_user()
            if not user:
                return self._send_json({"error": "Unauthorized"}, 401)
            return self._send_json({"user": row_to_user(user)})

        if path == "/api/paypal/config":
            config = load_paypal_config()
            return self._send_json(
                {
                    "clientId": config["client_id"],
                    "currency": config["currency"],
                    "mode": config["mode"],
                    "configured": bool(config["client_id"] and config["secret"]),
                }
            )

        if path == "/api/orders":
            if not self._require_auth():
                return
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return self._send_json([row_to_order(r) for r in rows])

        if path == "/api/products":
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM products ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return self._send_json([row_to_product(r) for r in rows])

        if path.startswith("/api/products/"):
            pid = path.split("/api/products/", 1)[1]
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            if not row:
                return self._send_json({"error": "Not found"}, 404)
            return self._send_json(row_to_product(row))

        if path == "/api/posts":
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM posts ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return self._send_json([row_to_post(r) for r in rows])

        if path.startswith("/api/posts/"):
            pid = path.split("/api/posts/", 1)[1]
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM posts WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            if not row:
                return self._send_json({"error": "Not found"}, 404)
            return self._send_json(row_to_post(row))

        if not is_allowed_static_path(path):
            return self._send_json({"error": "Not found"}, 404)

        return super().do_GET()

    # ---------- POST ----------
    def do_POST(self):
        path = urlparse(self.path).path

        # --- Upload a product image (raw bytes body, not JSON) ---
        if path == "/api/upload":
            if not self._require_auth():
                return
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = UPLOAD_CONTENT_TYPES.get(content_type)
            if not ext:
                return self._send_json(
                    {"error": "Unsupported image type. Use PNG, JPG, WEBP, or GIF."}, 400
                )
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                return self._send_json({"error": "Empty upload"}, 400)
            if length > MAX_UPLOAD_BYTES:
                return self._send_json({"error": "Image too large (max 8MB)"}, 413)
            body = self.rfile.read(length)
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"upload-{int(time.time() * 1000)}-{secrets.token_hex(4)}.{ext}"
            (UPLOADS_DIR / filename).write_bytes(body)
            return self._send_json({"path": f"assets/uploads/{filename}"}, 201)

        data = self._read_json()

        # --- PayPal: create order for a cart (amounts always looked up server-side) ---
        if path == "/api/paypal/orders":
            config = load_paypal_config()
            if not config["client_id"] or not config["secret"]:
                return self._send_json(
                    {"error": "PayPal is not configured yet. Add your Client ID and Secret to paypal_config.json."},
                    503,
                )

            cart_items = data.get("items")
            if not isinstance(cart_items, list) or not cart_items:
                return self._send_json({"error": "Cart is empty"}, 400)

            conn = get_db()
            line_items = []
            total = 0.0
            for entry in cart_items:
                pid = entry.get("productId")
                try:
                    qty = int(entry.get("quantity", 1))
                except (TypeError, ValueError):
                    qty = 0
                if qty < 1:
                    conn.close()
                    return self._send_json({"error": "Invalid quantity in cart"}, 400)
                product = conn.execute(
                    "SELECT * FROM products WHERE id = ?", (pid,)
                ).fetchone()
                if not product:
                    conn.close()
                    return self._send_json({"error": f"Product not found: {pid}"}, 404)
                if product["status"] == "Sold Out":
                    conn.close()
                    return self._send_json(
                        {"error": f"“{product['name']}” is sold out"}, 400
                    )
                unit_price = float(product["price"])
                line_items.append(
                    {
                        "productId": product["id"],
                        "productName": product["name"],
                        "quantity": qty,
                        "unitPrice": unit_price,
                    }
                )
                total += unit_price * qty
            conn.close()

            try:
                token = paypal_get_token(config)
                order = paypal_api(
                    "POST",
                    "/v2/checkout/orders",
                    config,
                    token,
                    {
                        "intent": "CAPTURE",
                        "purchase_units": [
                            {
                                "reference_id": "cart",
                                "amount": {
                                    "currency_code": config["currency"],
                                    "value": f"{total:.2f}",
                                    "breakdown": {
                                        "item_total": {
                                            "currency_code": config["currency"],
                                            "value": f"{total:.2f}",
                                        }
                                    },
                                },
                                "items": [
                                    {
                                        "name": li["productName"][:127],
                                        "sku": li["productId"],
                                        "quantity": str(li["quantity"]),
                                        "unit_amount": {
                                            "currency_code": config["currency"],
                                            "value": f"{li['unitPrice']:.2f}",
                                        },
                                    }
                                    for li in line_items
                                ],
                            }
                        ],
                    },
                )
            except urllib.error.HTTPError as e:
                print(f"PayPal create-order error: {e.code} {e.read().decode(errors='replace')}")
                return self._send_json({"error": "PayPal order creation failed"}, 502)
            except Exception as e:
                print(f"PayPal create-order error: {e}")
                return self._send_json({"error": "PayPal order creation failed"}, 502)

            with PENDING_CARTS_LOCK:
                PENDING_CARTS[order["id"]] = line_items

            return self._send_json({"id": order["id"]}, 201)

        # --- PayPal: capture an approved order ---
        if path.startswith("/api/paypal/orders/") and path.endswith("/capture"):
            config = load_paypal_config()
            if not config["client_id"] or not config["secret"]:
                return self._send_json({"error": "PayPal is not configured"}, 503)
            order_id = path.split("/api/paypal/orders/", 1)[1].rsplit("/capture", 1)[0]

            try:
                token = paypal_get_token(config)
                capture = paypal_api(
                    "POST", f"/v2/checkout/orders/{order_id}/capture", config, token, {}
                )
            except urllib.error.HTTPError as e:
                print(f"PayPal capture error: {e.code} {e.read().decode(errors='replace')}")
                return self._send_json({"error": "Payment could not be captured"}, 502)
            except Exception as e:
                print(f"PayPal capture error: {e}")
                return self._send_json({"error": "Payment could not be captured"}, 502)

            status = capture.get("status", "UNKNOWN")
            payer = capture.get("payer", {})
            payer_name = " ".join(
                filter(None, [
                    (payer.get("name") or {}).get("given_name"),
                    (payer.get("name") or {}).get("surname"),
                ])
            ) or None
            payer_email = payer.get("email_address")

            with PENDING_CARTS_LOCK:
                line_items = PENDING_CARTS.pop(order_id, None)

            if not line_items:
                # Server restarted between create and capture, or a stale/unknown
                # order id — fall back to a single record from PayPal's own total
                # so the payment is never silently lost from order history.
                purchase_unit = (capture.get("purchase_units") or [{}])[0]
                captures = (purchase_unit.get("payments") or {}).get("captures") or [{}]
                amount_info = captures[0].get("amount", {})
                line_items = [
                    {
                        "productId": None,
                        "productName": "Cart order",
                        "quantity": 1,
                        "unitPrice": float(amount_info.get("value", 0) or 0),
                    }
                ]

            conn = get_db()
            now = int(time.time() * 1000)
            order_row_ids = []
            for idx, li in enumerate(line_items):
                order_row_id = f"order-{now}-{idx}"
                order_row_ids.append(order_row_id)
                conn.execute(
                    """INSERT INTO orders
                       (id, product_id, product_name, amount, currency, payer_email, payer_name, paypal_order_id, status, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        order_row_id,
                        li["productId"],
                        li["productName"],
                        li["unitPrice"] * li["quantity"],
                        config["currency"],
                        payer_email,
                        payer_name,
                        order_id,
                        status,
                        now,
                    ),
                )
            conn.commit()
            conn.close()

            return self._send_json({"status": status, "orderIds": order_row_ids})

        # --- Login ---
        if path == "/api/login":
            username = (data.get("username") or "").strip()
            password = data.get("password") or ""
            if not username or not password:
                return self._send_json(
                    {"error": "Username and password required"}, 400
                )

            conn = get_db()
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            if not user:
                conn.close()
                return self._send_json({"error": "Invalid credentials"}, 401)

            expected = hash_password(password, user["salt"])
            if not secrets.compare_digest(expected, user["password_hash"]):
                conn.close()
                return self._send_json({"error": "Invalid credentials"}, 401)

            # Create session token
            token = secrets.token_urlsafe(32)
            now = int(time.time() * 1000)
            expires = now + SESSION_DAYS * 24 * 60 * 60 * 1000
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                (token, user["id"], now, expires),
            )
            conn.commit()
            conn.close()

            return self._send_json(
                {
                    "ok": True,
                    "token": token,
                    "user": row_to_user(user),
                    "expiresAt": expires,
                }
            )

        # --- Logout ---
        if path == "/api/logout":
            token = self._bearer_token()
            if token:
                conn = get_db()
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
                conn.close()
            return self._send_json({"ok": True})

        # --- Products (auth required for create) ---
        if path == "/api/products":
            if not self._require_auth():
                return
            pid = data.get("id") or f"prod-{int(time.time() * 1000)}"
            now = int(time.time() * 1000)
            conn = get_db()
            conn.execute(
                """INSERT INTO products
                   (id, name, price, category, status, description, image, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    pid,
                    data["name"],
                    float(data["price"]),
                    data["category"],
                    data.get("status", "Available"),
                    data["description"],
                    data.get("image", "assets/suspension.png"),
                    data.get("createdAt", now),
                    None,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_product(row), 201)

        # --- Posts (auth required for create) ---
        if path == "/api/posts":
            if not self._require_auth():
                return
            pid = data.get("id") or f"post-{int(time.time() * 1000)}"
            now = int(time.time() * 1000)
            tags = data.get("tags", [])
            if isinstance(tags, list):
                tags = json.dumps(tags)
            conn = get_db()
            conn.execute(
                """INSERT INTO posts
                   (id, title, tags, content, date, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    pid,
                    data["title"],
                    tags,
                    data["content"],
                    data.get("date", time.strftime("%Y-%m-%d")),
                    data.get("createdAt", now),
                    None,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM posts WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_post(row), 201)

        self._send_json({"error": "Not found"}, 404)

    # ---------- PUT ----------
    def do_PUT(self):
        path = urlparse(self.path).path
        data = self._read_json()
        now = int(time.time() * 1000)

        if path == "/api/me":
            user = self._require_auth()
            if not user:
                return
            current_password = data.get("currentPassword") or ""
            expected = hash_password(current_password, user["salt"])
            if not current_password or not secrets.compare_digest(expected, user["password_hash"]):
                return self._send_json({"error": "Current password is incorrect"}, 401)

            new_username = (data.get("newUsername") or "").strip() or user["username"]
            new_password = data.get("newPassword") or ""
            new_display_name = data.get("newDisplayName")
            new_display_name = new_display_name.strip() if new_display_name else None
            if new_display_name is None:
                new_display_name = user["display_name"]

            conn = get_db()
            if new_username != user["username"]:
                clash = conn.execute(
                    "SELECT id FROM users WHERE username = ? AND id != ?",
                    (new_username, user["id"]),
                ).fetchone()
                if clash:
                    conn.close()
                    return self._send_json({"error": "Username already taken"}, 409)

            if new_password:
                salt = secrets.token_hex(16)
                password_hash = hash_password(new_password, salt)
                conn.execute(
                    "UPDATE users SET username=?, password_hash=?, salt=?, display_name=? WHERE id=?",
                    (new_username, password_hash, salt, new_display_name, user["id"]),
                )
            else:
                conn.execute(
                    "UPDATE users SET username=?, display_name=? WHERE id=?",
                    (new_username, new_display_name, user["id"]),
                )

            # Keep the current session alive, but log out this user's other sessions
            current_token = self._bearer_token()
            conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token != ?",
                (user["id"], current_token),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            conn.close()
            return self._send_json({"ok": True, "user": row_to_user(row)})

        if path.startswith("/api/products/"):
            if not self._require_auth():
                return
            pid = path.split("/api/products/", 1)[1]
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM products WHERE id = ?", (pid,)
            ).fetchone()
            if not existing:
                conn.close()
                return self._send_json({"error": "Not found"}, 404)
            conn.execute(
                """UPDATE products SET name=?, price=?, category=?, status=?,
                   description=?, image=?, updated_at=? WHERE id=?""",
                (
                    data["name"],
                    float(data["price"]),
                    data["category"],
                    data.get("status", "Available"),
                    data["description"],
                    data.get("image", "assets/suspension.png"),
                    now,
                    pid,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_product(row))

        if path.startswith("/api/posts/"):
            if not self._require_auth():
                return
            pid = path.split("/api/posts/", 1)[1]
            tags = data.get("tags", [])
            if isinstance(tags, list):
                tags = json.dumps(tags)
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM posts WHERE id = ?", (pid,)
            ).fetchone()
            if not existing:
                conn.close()
                return self._send_json({"error": "Not found"}, 404)
            conn.execute(
                "UPDATE posts SET title=?, tags=?, content=?, updated_at=? WHERE id=?",
                (data["title"], tags, data["content"], now, pid),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM posts WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_post(row))

        if path.startswith("/api/orders/"):
            if not self._require_auth():
                return
            oid = path.split("/api/orders/", 1)[1]
            new_status = data.get("fulfillmentStatus")
            if new_status not in FULFILLMENT_STATUSES:
                return self._send_json(
                    {"error": f"fulfillmentStatus must be one of {sorted(FULFILLMENT_STATUSES)}"},
                    400,
                )
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM orders WHERE id = ?", (oid,)
            ).fetchone()
            if not existing:
                conn.close()
                return self._send_json({"error": "Not found"}, 404)
            conn.execute(
                "UPDATE orders SET fulfillment_status = ? WHERE id = ?", (new_status, oid)
            )
            conn.commit()
            row = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
            conn.close()
            return self._send_json(row_to_order(row))

        self._send_json({"error": "Not found"}, 404)

    # ---------- DELETE ----------
    def do_DELETE(self):
        path = urlparse(self.path).path

        if path.startswith("/api/products/"):
            if not self._require_auth():
                return
            pid = path.split("/api/products/", 1)[1]
            conn = get_db()
            conn.execute("DELETE FROM products WHERE id = ?", (pid,))
            conn.commit()
            conn.close()
            return self._send_json({"ok": True})

        if path.startswith("/api/posts/"):
            if not self._require_auth():
                return
            pid = path.split("/api/posts/", 1)[1]
            conn = get_db()
            conn.execute("DELETE FROM posts WHERE id = ?", (pid,))
            conn.commit()
            conn.close()
            return self._send_json({"ok": True})

        if path.startswith("/api/orders/"):
            if not self._require_auth():
                return
            oid = path.split("/api/orders/", 1)[1]
            conn = get_db()
            conn.execute("DELETE FROM orders WHERE id = ?", (oid,))
            conn.commit()
            conn.close()
            return self._send_json({"ok": True})

        self._send_json({"error": "Not found"}, 404)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}")


def main():
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        return

    # Ensure users table exists
    conn = get_db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            display_name TEXT,
            created_at INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            product_id TEXT,
            product_name TEXT,
            amount REAL,
            currency TEXT,
            payer_email TEXT,
            payer_name TEXT,
            paypal_order_id TEXT,
            status TEXT,
            created_at INTEGER NOT NULL
        )"""
    )
    order_cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    if "fulfillment_status" not in order_cols:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN fulfillment_status TEXT NOT NULL DEFAULT 'Pending'"
        )
    conn.commit()
    conn.close()

    if not PAYPAL_CONFIG_PATH.exists():
        with open(PAYPAL_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"mode": "sandbox", "client_id": "", "secret": "", "currency": "USD"}, f, indent=2)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Rabithole server running at http://localhost:{PORT}")
    print(f"Database: {DB_PATH}")
    print("Default login - username: admin  password: rabithole")
    paypal_config = load_paypal_config()
    if not paypal_config["client_id"] or not paypal_config["secret"]:
        print("PayPal is NOT configured - edit paypal_config.json with your Client ID/Secret to enable checkout.")
    else:
        print(f"PayPal checkout enabled ({paypal_config['mode']} mode)")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
