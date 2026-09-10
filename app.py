import base64
import datetime as dt
import hashlib
import hmac
import secrets
import time
import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import quote

import streamlit as st
try:
    from streamlit_geolocation import streamlit_geolocation
except ImportError:
    streamlit_geolocation = None

BASE_DIR = Path(__file__).resolve().parent
BRAND_ICON = next((BASE_DIR / name for name in ["icone.png", "icone.jpg", "logo.png", "logo.svg"] if (BASE_DIR / name).exists()), None)
st.set_page_config(page_title="Lubumarket — Lubumbashi", page_icon=str(BRAND_ICON) if BRAND_ICON else "🛍️", layout="wide")
DB_NAME = BASE_DIR / "hyperlocal_shop.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DEFAULT_NEIGHBORHOOD = "Tous"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return salt.hex() + ":" + digest.hex()


def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000).hex()
        return hmac.compare_digest(candidate, digest_hex)
    except (ValueError, AttributeError):
        return False


def init_db():
    with get_connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            name TEXT NOT NULL,
            address TEXT,
            latitude REAL NOT NULL DEFAULT -11.6680,
            longitude REAL NOT NULL DEFAULT 27.4850,
            whatsapp TEXT NOT NULL,
            is_open INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL CHECK(price >= 0),
            image_url TEXT,
            stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0),
            FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS group_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            neighborhood TEXT NOT NULL,
            current_participants INTEGER NOT NULL DEFAULT 0,
            target_participants INTEGER NOT NULL DEFAULT 5,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_order_id INTEGER NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(group_order_id, phone),
            FOREIGN KEY(group_order_id) REFERENCES group_orders(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS creators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            specialty TEXT NOT NULL,
            neighborhood TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE,
            description TEXT,
            password_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            neighborhood TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS short_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER,
            shop_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            video_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(creator_id) REFERENCES creators(id) ON DELETE CASCADE,
            FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS road_conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area TEXT NOT NULL UNIQUE,
            condition TEXT NOT NULL DEFAULT 'Normale',
            note TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notification_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            phone TEXT NOT NULL,
            neighborhood TEXT,
            shop_id INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(phone, shop_id, neighborhood),
            FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE,
            FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            link_type TEXT,
            read_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS creator_designs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            fabric TEXT,
            price REAL NOT NULL DEFAULT 0,
            image_path TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(creator_id) REFERENCES creators(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS courier_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            neighborhood TEXT NOT NULL,
            shop_name TEXT NOT NULL,
            product_name TEXT NOT NULL,
            sizes TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'Demande envoyée',
            created_at TEXT NOT NULL
        );
        """)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(shops)").fetchall()]
        if "owner_id" not in columns:
            conn.execute("ALTER TABLE shops ADD COLUMN owner_id INTEGER")
        product_columns = [row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()]
        if "media_type" not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN media_type TEXT NOT NULL DEFAULT 'photo'")
        design_columns = [row[1] for row in conn.execute("PRAGMA table_info(creator_designs)").fetchall()]
        if "media_type" not in design_columns:
            conn.execute("ALTER TABLE creator_designs ADD COLUMN media_type TEXT NOT NULL DEFAULT 'photo'")
        if conn.execute("SELECT COUNT(*) FROM shops").fetchone()[0] == 0:
            conn.executemany("INSERT INTO shops (name, address, latitude, longitude, whatsapp) VALUES (?, ?, ?, ?, ?)", [
                ("Boutique du Coin", "Quartier Nord", -11.6650, 27.4830, "243812345678"),
                ("Supérette Centre", "Avenue Centrale", -11.6700, 27.4900, "243898765432")])
            conn.executemany("INSERT INTO products (shop_id, name, description, price, image_url, stock) VALUES (?, ?, ?, ?, ?, ?)", [
                (1, "Robe d'été fleurie", "Taille M, tissu léger de qualité", 25, "https://images.unsplash.com/photo-1496747611176-843222e1e57c?w=800", 5),
                (1, "Sandales en cuir", "Pointure 39, fabrication artisanale", 15, "https://images.unsplash.com/photo-1543163521-1bf539c55dd2?w=800", 2),
                (2, "Sac à main chic", "Couleur noire, idéal pour les sorties", 40, "https://images.unsplash.com/photo-1584917865442-de89df76afd3?w=800", 3)])
            conn.execute("INSERT INTO group_orders (product_id, neighborhood, target_participants) VALUES (1, 'Quartier Nord', 5)")
            conn.execute("INSERT INTO stories (shop_id, content, created_at) VALUES (1, ?, ?)", ("Nouvel arrivage de robes ce matin !", dt.datetime.now().strftime("%Y-%m-%d %H:%M")))


def normalize_text(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def neighborhood_matches(wanted, address, shop_name=""):
    wanted = normalize_text(wanted)
    if not wanted or wanted in {"tous", "toutes", "tous les quartiers"}:
        return True
    return wanted in normalize_text(f"{address or ''} {shop_name or ''}")


def clean_phone(phone):
    """Convertit 081..., +24381... ou 24381... vers le même format."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "243" + digits[1:]
    if not digits.startswith("243") and len(digits) <= 9:
        digits = "243" + digits
    return digits


def wa_link(phone, message):
    number = clean_phone(phone)
    return f"https://wa.me/{number}?text={quote(message)}"


def save_uploaded_image(uploaded):
    if uploaded is None:
        return ""
    safe_name = Path(uploaded.name).name.replace(" ", "_")
    path = UPLOAD_DIR / f"{dt.datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe_name}"
    path.write_bytes(uploaded.getvalue())
    return str(path)


def save_uploaded_video(uploaded):
    if uploaded is None:
        return ""
    safe_name = Path(uploaded.name).name.replace(" ", "_")
    path = UPLOAD_DIR / f"video_{dt.datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe_name}"
    path.write_bytes(uploaded.getvalue())
    return str(path)


def road_factor(condition):
    return {"Bonne": 1.0, "Normale": 1.15, "Mauvaise": 1.45, "Très mauvaise": 1.8}.get(condition, 1.15)


def transport_estimate(distance_km, mode, condition):
    base = {"Moto": (1.5, 0.45), "Taxi-bus": (0.8, 0.25), "Taxi": (2.0, 0.65)}.get(mode, (1.0, 0.35))
    fixed, per_km = base
    factor = road_factor(condition)
    cost = (fixed + distance_km * per_km) * factor
    speed = {"Moto": 28, "Taxi-bus": 16, "Taxi": 22}.get(mode, 20) / factor
    minutes = max(3, round((distance_km / speed) * 60))
    return round(cost, 2), minutes


def show_transport_calculator():
    st.subheader("Calculateur de transport à Lubumbashi")
    st.caption("Estimation indicative : l’état des routes est déclaré manuellement et doit être confirmé par le conducteur.")
    c1, c2 = st.columns(2)
    distance = c1.number_input("Distance estimée en kilomètres", min_value=0.1, value=3.0, step=0.5)
    mode = c2.selectbox("Moyen de transport", ["Moto", "Taxi-bus", "Taxi"])
    area = st.text_input("Zone ou route", placeholder="Golf, Kenya, Katuba...")
    condition = st.selectbox("État actuel de la route", ["Bonne", "Normale", "Mauvaise", "Très mauvaise"])
    if st.button("Calculer le trajet et le coût estimé", use_container_width=True):
        cost, minutes = transport_estimate(distance, mode, condition)
        st.success(f"Trajet estimé : environ {minutes} minutes · coût indicatif : {cost:.2f} $ en {mode}.")
        st.info("Itinéraire conseillé : privilégier les axes principaux et éviter les routes déclarées mauvaises. Cette version ne reçoit pas encore les embouteillages en temps réel.")
        if area.strip():
            with get_connection() as conn:
                conn.execute("INSERT INTO road_conditions (area, condition, note, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(area) DO UPDATE SET condition=excluded.condition, updated_at=excluded.updated_at", (area.strip(), condition, "État déclaré dans le calculateur", dt.datetime.now().isoformat(timespec="minutes")))


def customer_auth():
    st.subheader("Compte utilisateur")
    login, register = st.tabs(["Se connecter", "Créer un compte"])
    with login:
        with st.form("customer_login"):
            phone = st.text_input("Téléphone", key="cust_login_phone")
            password = st.text_input("Mot de passe", type="password", key="cust_login_password")
            if st.form_submit_button("Se connecter"):
                with get_connection() as conn:
                    user = conn.execute("SELECT * FROM customers WHERE phone=?", (clean_phone(phone),)).fetchone()
                if user and verify_password(password, user["password_hash"]):
                    st.session_state.customer_id = user["id"]
                    st.session_state.customer_name = user["full_name"]
                    st.success("Connexion réussie.")
                    st.rerun()
                else:
                    st.error("Téléphone ou mot de passe incorrect.")
    with register:
        with st.form("customer_register"):
            name = st.text_input("Nom complet", key="cust_reg_name")
            phone = st.text_input("Téléphone", key="cust_reg_phone")
            password = st.text_input("Mot de passe — 8 caractères minimum", type="password", key="cust_reg_password")
            confirm = st.text_input("Confirmer le mot de passe", type="password", key="cust_reg_confirm")
            neighborhood = st.text_input("Quartier", key="cust_reg_neighborhood")
            if st.form_submit_button("Créer mon compte"):
                if not all(v.strip() for v in [name, phone, password, neighborhood]) or len(password) < 8 or password != confirm:
                    st.error("Remplissez tous les champs, utilisez 8 caractères minimum et confirmez le mot de passe.")
                else:
                    try:
                        with get_connection() as conn:
                            conn.execute("INSERT INTO customers (full_name, phone, password_hash, neighborhood, created_at) VALUES (?, ?, ?, ?, ?)", (name.strip(), clean_phone(phone), hash_password(password), neighborhood.strip(), dt.datetime.now().isoformat(timespec="minutes")))
                        st.success("Compte créé. Vous pouvez maintenant vous connecter.")
                    except sqlite3.IntegrityError:
                        st.error("Ce numéro possède déjà un compte utilisateur.")


def show_short_videos():
    st.subheader("Défilé Lubumarket")
    st.caption("Vidéos courtes de cinq secondes environ : vêtements portés en mouvement à Lubumbashi.")
    with get_connection() as conn:
        videos = conn.execute("""SELECT v.*, c.full_name creator_name, s.name shop_name
        FROM short_videos v LEFT JOIN creators c ON v.creator_id=c.id LEFT JOIN shops s ON v.shop_id=s.id ORDER BY v.id DESC""").fetchall()
    if not videos:
        st.info("Aucune vidéo publiée pour le moment.")
    for video in videos:
        with st.container(border=True):
            st.markdown(f"### {video['title']}")
            if Path(video["video_path"]).exists():
                st.video(video["video_path"])
            st.write(video["description"] or "Découvrez ce modèle en mouvement.")
            st.caption(video["creator_name"] or video["shop_name"] or "Créateur local")


def haversine_km(lat1, lon1, lat2, lon2):
    from math import asin, cos, radians, sin, sqrt
    dlat = radians(float(lat2) - float(lat1))
    dlon = radians(float(lon2) - float(lon1))
    a = sin(dlat / 2) ** 2 + cos(radians(float(lat1))) * cos(radians(float(lat2))) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def get_client_location():
    if streamlit_geolocation is None:
        st.info("La géolocalisation automatique sera disponible après l’installation du module streamlit-geolocation. Vous pouvez continuer avec la recherche par quartier.")
        return None
    location = streamlit_geolocation()
    if location and location.get("latitude") is not None and location.get("longitude") is not None:
        return float(location["latitude"]), float(location["longitude"])
    return None


def load_nearby_products(location, max_minutes=15):
    if not location:
        return []
    lat, lon = location
    products = load_products()
    result = []
    for product in products:
        distance = haversine_km(lat, lon, product["latitude"], product["longitude"]) if "latitude" in product.keys() else 999
        minutes = round((distance / 20) * 60)
        if minutes <= max_minutes:
            result.append((product, distance, minutes))
    return result


def load_products():
    with get_connection() as conn:
        return conn.execute("""SELECT p.*, s.name shop_name, s.address, s.whatsapp, s.is_open,
        s.latitude, s.longitude FROM products p JOIN shops s ON p.shop_id=s.id ORDER BY p.id DESC""").fetchall()


def show_cart():
    cart = st.session_state.setdefault("cart", {})
    with st.expander(f"Panier ({sum(i['qty'] for i in cart.values())} article(s))", expanded=bool(cart)):
        if not cart:
            st.info("Votre panier est vide.")
            return
        grouped = {}
        for item in cart.values():
            grouped.setdefault(item["shop_id"], {"shop_name": item["shop_name"], "whatsapp": item["whatsapp"], "items": []})["items"].append(item)
        for shop_id, group in grouped.items():
            st.markdown(f"#### Commande pour {group['shop_name']}")
            total = 0
            lines = [f"Bonjour {group['shop_name']}, je souhaite commander :"]
            for item in group["items"]:
                subtotal = item["price"] * item["qty"]
                total += subtotal
                lines.append(f"- {item['name']} x{item['qty']} = {subtotal:.2f} $")
                c1, c2, c3 = st.columns([5, 1, 1])
                c1.write(f"{item['name']} x{item['qty']} — {subtotal:.2f} $")
                if c2.button("+", key=f"plus_{item['id']}"):
                    item["qty"] += 1
                    st.rerun()
                if c3.button("Retirer", key=f"remove_{item['id']}"):
                    del cart[item["id"]]
                    st.rerun()
            lines.append(f"Total estimé : {total:.2f} $")
            st.write(f"**Total pour cette boutique : {total:.2f} $**")
            st.link_button("Envoyer cette commande sur WhatsApp", wa_link(group["whatsapp"], "\n".join(lines)), use_container_width=True)
        if st.button("Vider tout le panier", key="clear_cart"):
            st.session_state.cart = {}
            st.rerun()


def add_to_cart(p):
    cart = st.session_state.setdefault("cart", {})
    key = str(p["id"])
    if key not in cart:
        cart[key] = {"id": key, "name": p["name"], "shop_id": p["shop_id"], "shop_name": p["shop_name"], "whatsapp": p["whatsapp"], "price": float(p["price"]), "qty": 0}
    cart[key]["qty"] = min(cart[key]["qty"] + 1, int(p["stock"]))


def show_client():
    show_cart()
    st.subheader("Rechercher et découvrir les articles")
    st.caption("Utilisez les champs ci-dessous pour rechercher un article et choisir votre quartier.")
    c1, c2, c3 = st.columns([2, 1.4, 1])
    query = c1.text_input("Rechercher un article", placeholder="Robe, sac, sandales...").lower().strip()
    neighborhood = c2.text_input("Quartier — écrivez Tous pour tout afficher", value=DEFAULT_NEIGHBORHOOD, placeholder="Golf, Kenya, Katuba...")
    only_open = c3.checkbox("Boutiques ouvertes")
    use_gps = st.checkbox("Utiliser ma position pour voir les boutiques à environ 15 minutes")
    location = get_client_location() if use_gps else None
    all_products = load_products()
    nearby_ids = {p["id"] for p, _, _ in load_nearby_products(location)} if location else None
    products = [p for p in all_products if (nearby_ids is None or p["id"] in nearby_ids) and neighborhood_matches(neighborhood, p["address"], p["shop_name"]) and query in normalize_text(f"{p['name']} {p['description']}") and (not only_open or p["is_open"])]
    if use_gps and location:
        st.success("Position reçue. Les boutiques situées à environ 15 minutes sont affichées.")
    elif use_gps:
        st.info("Autorisez la localisation dans votre navigateur pour afficher les boutiques proches.")
    if not products and neighborhood.strip() and normalize_text(neighborhood) not in {"tous", "toutes"}:
        # Pour une démonstration, afficher les produits de test si aucun commerce
        # n'est encore enregistré dans le quartier demandé.
        products = [p for p in all_products if query in normalize_text(f"{p['name']} {p['description']}") and (not only_open or p["is_open"])]
        if products:
            st.info("Aucun commerce n’est encore enregistré dans ce quartier. Voici les produits disponibles pour la démonstration.")
    if products:
        cols = st.columns(3)
        for i, p in enumerate(products):
            with cols[i % 3]:
                if p["image_url"]:
                    try:
                        if p["media_type"] == "video":
                            st.video(p["image_url"])
                        else:
                            st.image(p["image_url"], use_container_width=True)
                    except Exception:
                        st.caption("Média indisponible")
                st.markdown(f"### {p['name']}")
                st.write(p["description"] or "Aucune description.")
                st.markdown(f"**{p['price']:.2f} $** · Stock : **{p['stock']}**")
                st.caption(f"{p['shop_name']} · {p['address'] or 'Quartier non indiqué'}")
                if p["stock"]:
                    if st.button("Ajouter au panier", key=f"add_{p['id']}", use_container_width=True):
                        add_to_cart(p)
                        st.success("Article ajouté au panier.")
                        st.rerun()
                else:
                    st.warning("Rupture de stock")
    else:
        st.warning("Aucun produit trouvé. Essayez le mot « Tous » ou vérifiez l’orthographe du quartier.")


def create_arrival_notifications(shop_id, shop_name, neighborhood, content):
    title = f"Nouvel arrivage chez {shop_name}"
    message = f"{content} — quartier {neighborhood}"
    with get_connection() as conn:
        subscribers = conn.execute("SELECT phone FROM notification_subscriptions WHERE shop_id=? OR lower(neighborhood)=lower(?) OR neighborhood IS NULL", (shop_id, neighborhood)).fetchall()
        for subscriber in subscribers:
            conn.execute("INSERT INTO notifications (phone, title, message, link_type, created_at) VALUES (?, ?, ?, ?, ?)", (subscriber["phone"], title, message, "arrivages", dt.datetime.now().isoformat(timespec="minutes")))


def show_notifications():
    customer_phone = None
    if st.session_state.get("customer_id"):
        with get_connection() as conn:
            row = conn.execute("SELECT phone FROM customers WHERE id=?", (st.session_state["customer_id"],)).fetchone()
        customer_phone = row["phone"] if row else None
    st.subheader("Notifications d’arrivage")
    if not customer_phone:
        st.info("Créez ou ouvrez un compte utilisateur pour recevoir les notifications dans l’application.")
        return
    with get_connection() as conn:
        notes = conn.execute("SELECT * FROM notifications WHERE phone=? ORDER BY id DESC LIMIT 30", (customer_phone,)).fetchall()
    if not notes:
        st.info("Vous n’avez pas encore de notification.")
    for note in notes:
        with st.container(border=True):
            st.markdown(f"**{note['title']}**")
            st.write(note["message"])
            st.caption(note["created_at"])


def show_notification_subscription():
    st.subheader("Recevoir les alertes d’arrivage")
    st.caption("Vous recevrez une notification dans l’application lorsqu’un vendeur publiera un nouvel arrivage dans votre quartier ou dans une boutique suivie.")
    phone_default = ""
    if st.session_state.get("customer_id"):
        with get_connection() as conn:
            row = conn.execute("SELECT phone, neighborhood FROM customers WHERE id=?", (st.session_state["customer_id"],)).fetchone()
        if row:
            phone_default = row["phone"]
    with st.form("notification_subscription"):
        phone = st.text_input("Votre téléphone", value=phone_default)
        neighborhood = st.text_input("Quartier à suivre", placeholder="Golf, Kenya, Katuba...")
        with get_connection() as conn:
            shops = conn.execute("SELECT id, name FROM shops ORDER BY name").fetchall()
        shop_options = {"Tous les commerces": None, **{s["name"]: s["id"] for s in shops}}
        shop_name = st.selectbox("Boutique à suivre", list(shop_options.keys()))
        if st.form_submit_button("Activer les alertes"):
            if phone.strip() and (neighborhood.strip() or shop_options[shop_name] is None):
                try:
                    with get_connection() as conn:
                        conn.execute("INSERT INTO notification_subscriptions (customer_id, phone, neighborhood, shop_id, created_at) VALUES (?, ?, ?, ?, ?)", (st.session_state.get("customer_id"), clean_phone(phone), neighborhood.strip() or None, shop_options[shop_name], dt.datetime.now().isoformat(timespec="minutes")))
                    st.success("Alertes activées.")
                except sqlite3.IntegrityError:
                    st.info("Cette alerte est déjà activée.")
            else:
                st.error("Indiquez votre téléphone et un quartier ou une boutique.")


def show_arrivals():
    st.subheader("Arrivages à Lubumbashi")
    with get_connection() as conn:
        arrivals = conn.execute("""SELECT stories.*, shops.name shop_name, shops.address, shops.whatsapp
        FROM stories JOIN shops ON stories.shop_id=shops.id ORDER BY stories.id DESC LIMIT 20""").fetchall()
    if not arrivals:
        st.info("Aucun arrivage publié pour le moment.")
    for arrival in arrivals:
        with st.container(border=True):
            st.markdown(f"**{arrival['shop_name']} — {arrival['address']}**")
            st.write(arrival["content"])
            st.caption(f"Publié le {arrival['created_at']}")
            st.link_button("Contacter la boutique", wa_link(arrival["whatsapp"], f"Bonjour, je viens de voir votre arrivage : {arrival['content']}"))


def show_group_orders():
    st.subheader("Commandes groupées par quartier")
    st.caption("Rejoignez une commande avec vos voisins pour discuter d’une livraison groupée et partager les frais de transport.")
    with get_connection() as conn:
        groups = conn.execute("""SELECT g.*, p.name product_name, p.price, s.name shop_name, s.whatsapp
        FROM group_orders g JOIN products p ON g.product_id=p.id JOIN shops s ON p.shop_id=s.id
        ORDER BY g.id DESC""").fetchall()
    if not groups:
        st.info("Aucune commande groupée active.")
    for group in groups:
        with st.container(border=True):
            st.markdown(f"**{group['product_name']} — {group['shop_name']}**")
            st.write(f"Quartier : **{group['neighborhood']}** · Participants : **{group['current_participants']}/{group['target_participants']}**")
            with st.form(f"join_group_{group['id']}"):
                name = st.text_input("Votre nom", key=f"group_name_{group['id']}")
                phone = st.text_input("Votre téléphone", key=f"group_phone_{group['id']}")
                if st.form_submit_button("Rejoindre cette commande"):
                    if name.strip() and phone.strip():
                        try:
                            with get_connection() as conn:
                                conn.execute("INSERT INTO group_members (group_order_id, customer_name, phone, created_at) VALUES (?, ?, ?, ?)", (group['id'], name.strip(), clean_phone(phone), dt.datetime.now().isoformat(timespec="minutes")))
                                conn.execute("UPDATE group_orders SET current_participants=current_participants+1 WHERE id=?", (group['id'],))
                            st.success("Vous avez rejoint la commande groupée.")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.warning("Ce numéro participe déjà à cette commande.")
                    else:
                        st.error("Indiquez votre nom et votre téléphone.")


def show_courier_request():
    st.subheader("Envoyer un coursier pour essayage")
    st.write("Le coursier peut apporter deux tailles. Vous essayez sur place et gardez la taille qui vous convient.")
    with st.form("courier_request"):
        c1, c2 = st.columns(2)
        customer = c1.text_input("Votre nom")
        phone = c2.text_input("Votre téléphone")
        neighborhood = st.text_input("Votre quartier")
        shop = st.text_input("Boutique concernée")
        product = st.text_input("Article à essayer")
        sizes = st.text_input("Deux tailles souhaitées", placeholder="M et L")
        note = st.text_area("Précisions pour le coursier")
        if st.form_submit_button("Envoyer la demande de coursier"):
            if all(v.strip() for v in [customer, phone, neighborhood, shop, product, sizes]):
                with get_connection() as conn:
                    conn.execute("INSERT INTO courier_requests (customer_name, phone, neighborhood, shop_name, product_name, sizes, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (customer.strip(), clean_phone(phone), neighborhood.strip(), shop.strip(), product.strip(), sizes.strip(), note.strip(), dt.datetime.now().isoformat(timespec="minutes")))
                st.success("Demande envoyée. La boutique doit maintenant confirmer la disponibilité du coursier.")
            else:
                st.error("Remplissez tous les champs obligatoires.")


def show_creators():
    st.subheader("Créateurs & Stylistes")
    st.caption("Découvrez les modèles locaux, le pagne et les créations sur mesure de Lubumbashi.")
    tab_browse, tab_register = st.tabs(["Découvrir les créations", "Proposer mes modèles"])
    with tab_browse:
        with get_connection() as conn:
            designs = conn.execute("""SELECT d.*, c.full_name, c.specialty, c.neighborhood, c.phone
            FROM creator_designs d JOIN creators c ON d.creator_id=c.id ORDER BY d.id DESC""").fetchall()
        if not designs:
            st.info("Aucun modèle n’a encore été publié.")
        cols = st.columns(3)
        for i, design in enumerate(designs):
            with cols[i % 3]:
                if design["image_path"]:
                    if design["media_type"] == "video":
                        st.video(design["image_path"])
                    else:
                        st.image(design["image_path"], use_container_width=True)
                st.markdown(f"### {design['name']}")
                st.write(design["description"] or "Création locale.")
                st.caption(f"{design['full_name']} · {design['specialty']} · {design['neighborhood']}")
                st.write(f"Prix indicatif : {design['price']:.2f} $ · Tissu : {design['fabric'] or 'à confirmer'}")
                st.link_button("Demander ce modèle", wa_link(design["phone"], f"Bonjour {design['full_name']}, je souhaite des informations sur votre modèle {design['name']}."))
    with tab_register:
        with st.form("creator_registration"):
            name = st.text_input("Nom du créateur ou styliste")
            specialty = st.selectbox("Spécialité", ["Styliste", "Tailleur", "Créateur de pagne", "Accessoires"])
            neighborhood = st.text_input("Quartier à Lubumbashi")
            phone = st.text_input("Téléphone / WhatsApp")
            description = st.text_area("Présentation")
            password = st.text_input("Mot de passe créateur — 8 caractères minimum", type="password")
            confirm = st.text_input("Confirmer le mot de passe", type="password")
            if st.form_submit_button("Créer mon profil créateur"):
                if all(v.strip() for v in [name, neighborhood, phone, password]) and len(password) >= 8 and password == confirm:
                    try:
                        with get_connection() as conn:
                            conn.execute("INSERT INTO creators (full_name, specialty, neighborhood, phone, description, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (name.strip(), specialty, neighborhood.strip(), clean_phone(phone), description.strip(), hash_password(password), dt.datetime.now().isoformat(timespec="minutes")))
                        st.success("Profil créé. Connectez-vous avec votre téléphone et votre mot de passe pour publier.")
                    except sqlite3.IntegrityError:
                        st.error("Ce numéro est déjà utilisé par un créateur.")
                else:
                    st.error("Remplissez les champs, utilisez 8 caractères minimum et confirmez le mot de passe.")
        with get_connection() as conn:
            creator = conn.execute("SELECT * FROM creators ORDER BY id DESC LIMIT 1").fetchone()
        if creator:
            st.markdown(f"**Profil créateur actif : {creator['full_name']}**")
            with st.form("creator_design"):
                design_name = st.text_input("Nom du modèle")
                design_description = st.text_area("Description du modèle")
                fabric = st.text_input("Tissu utilisé", placeholder="Pagne, wax, bazin...")
                price = st.number_input("Prix indicatif", min_value=0.0, step=1.0)
                media_type = st.radio("Représentation du modèle", ["photo", "video"], horizontal=True)
                media = st.file_uploader("Choisir le fichier", type=["jpg", "jpeg", "png", "webp"] if media_type == "photo" else ["mp4", "mov", "webm"])
                if st.form_submit_button("Publier le modèle"):
                    if design_name.strip() and media is not None:
                        saved_media = save_uploaded_image(media) if media_type == "photo" else save_uploaded_video(media)
                        with get_connection() as conn:
                            conn.execute("INSERT INTO creator_designs (creator_id, name, description, fabric, price, image_path, media_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (creator["id"], design_name.strip(), design_description.strip(), fabric.strip(), price, saved_media, media_type, dt.datetime.now().isoformat(timespec="minutes")))
                        st.success("Modèle publié.")
                        st.rerun()
            with st.form("creator_video"):
                video_title = st.text_input("Titre de la vidéo courte")
                video_description = st.text_area("Description de la vidéo")
                video = st.file_uploader("Vidéo verticale courte — MP4 recommandé", type=["mp4", "mov", "webm"], key="creator_video_upload")
                if st.form_submit_button("Publier dans le défilé"):
                    if video_title.strip() and video is not None:
                        with get_connection() as conn:
                            conn.execute("INSERT INTO short_videos (creator_id, title, description, video_path, created_at) VALUES (?, ?, ?, ?, ?)", (creator["id"], video_title.strip(), video_description.strip(), save_uploaded_video(video), dt.datetime.now().isoformat(timespec="minutes")))
                        st.success("Vidéo publiée dans le défilé.")
                        st.rerun()
    st.markdown("### Guide rapide des mesures")
    st.info("Mesurez sans serrer : tour de poitrine, tour de taille, tour de hanches, longueur souhaitée, longueur de manche et pointure. Envoyez ces mesures au créateur avec une photo de référence si nécessaire.")


def show_home():
    st.image(str(BASE_DIR / "logo.svg"), width=220)
    st.header("Bienvenue sur Lubumarket")
    st.write("Découvrez les produits des boutiques de Lubumbashi et commandez directement par WhatsApp.")
    st.info("Choisissez **Mode Client** pour rechercher des articles ou **Espace Commerçant** pour créer votre boutique.")


def auth_screen():
    st.subheader("Accès commerçant")
    login_tab, register_tab = st.tabs(["Se connecter", "Créer un compte commerçant"])
    with login_tab:
        with st.form("login"):
            phone = st.text_input("Numéro WhatsApp / téléphone", placeholder="243812345678")
            password = st.text_input("Mot de passe", type="password")
            if st.form_submit_button("Se connecter"):
                raw_phone = phone.strip()
                phone = clean_phone(raw_phone)
                with get_connection() as conn:
                    user = conn.execute("SELECT * FROM users WHERE phone=? OR phone=? OR phone=?", (phone, raw_phone, "+" + phone)).fetchone()
                if user and verify_password(password, user["password_hash"]):
                    st.session_state.user_id = user["id"]
                    st.session_state.user_name = user["full_name"]
                    st.success("Connexion réussie.")
                    st.rerun()
                else:
                    st.error("Numéro ou mot de passe incorrect.")
    with register_tab:
        st.info("L’inscription fonctionne depuis un téléphone : remplissez le formulaire et ajoutez votre boutique.")
        with st.form("register"):
            full_name = st.text_input("Votre nom complet")
            phone = st.text_input("Votre téléphone WhatsApp", placeholder="243812345678")
            password = st.text_input("Choisissez un mot de passe", type="password")
            confirm = st.text_input("Confirmez le mot de passe", type="password")
            shop_name = st.text_input("Nom de la boutique")
            neighborhood = st.text_input("Quartier / adresse", placeholder="Golf, Kenya, Katuba...")
            whatsapp = st.text_input("WhatsApp de la boutique", placeholder="243812345678")
            if st.form_submit_button("Créer mon compte et ma boutique"):
                if not all(x.strip() for x in [full_name, phone, password, shop_name, neighborhood, whatsapp]):
                    st.error("Tous les champs sont obligatoires.")
                elif len(password) < 8:
                    st.error("Le mot de passe doit contenir au moins 8 caractères.")
                elif password != confirm:
                    st.error("Les mots de passe ne correspondent pas.")
                else:
                    phone_clean = clean_phone(phone)
                    whatsapp_clean = clean_phone(whatsapp)
                    if len(phone_clean) < 12 or len(whatsapp_clean) < 12:
                        st.error("Utilisez un numéro valide, par exemple 243812345678.")
                    else:
                        try:
                            with get_connection() as conn:
                                cur = conn.execute("INSERT INTO users (full_name, phone, password_hash, created_at) VALUES (?, ?, ?, ?)", (full_name.strip(), phone_clean, hash_password(password), dt.datetime.now().isoformat(timespec="minutes")))
                                conn.execute("INSERT INTO shops (owner_id, name, address, whatsapp) VALUES (?, ?, ?, ?)", (cur.lastrowid, shop_name.strip(), neighborhood.strip(), whatsapp_clean))
                            st.success("Compte créé avec succès. Ouvrez l’onglet Se connecter pour entrer dans votre espace.")
                        except sqlite3.IntegrityError:
                            st.error("Ce numéro possède déjà un compte. Utilisez l’onglet Se connecter ou un autre numéro.")
                        except sqlite3.Error as error:
                            st.error(f"Impossible d’enregistrer le compte : {error}")


def show_merchant():
    user_id = st.session_state.get("user_id")
    if not user_id:
        auth_screen()
        return
    with get_connection() as conn:
        shops = conn.execute("SELECT * FROM shops WHERE owner_id=? ORDER BY name", (user_id,)).fetchall()
    st.success(f"Connecté : {st.session_state.get('user_name', '')}")
    if st.button("Se déconnecter"):
        st.session_state.pop("user_id", None)
        st.session_state.pop("user_name", None)
        st.rerun()
    if not shops:
        st.warning("Aucune boutique associée à ce compte.")
        return
    shop = shops[0]
    st.subheader(f"Gestion de {shop['name']}")
    with st.form("add_product"):
        name = st.text_input("Nom du produit")
        description = st.text_area("Description")
        c1, c2 = st.columns(2)
        price = c1.number_input("Prix en dollars", min_value=0.0, step=0.5)
        stock = c2.number_input("Stock", min_value=0, step=1)
        media_type = st.radio("Représentation de l’article", ["photo", "video"], horizontal=True)
        media = st.file_uploader("Choisir une photo ou une vidéo courte", type=["jpg", "jpeg", "png", "webp"] if media_type == "photo" else ["mp4", "mov", "webm"])
        if st.form_submit_button("Ajouter le produit"):
            if not name.strip() or media is None:
                st.error("Le nom et le fichier média sont obligatoires.")
            else:
                saved_media = save_uploaded_image(media) if media_type == "photo" else save_uploaded_video(media)
                with get_connection() as conn:
                    conn.execute("INSERT INTO products (shop_id, name, description, price, image_url, stock, media_type) VALUES (?, ?, ?, ?, ?, ?, ?)", (shop["id"], name.strip(), description.strip(), price, saved_media, stock, media_type))
                    if media_type == "video":
                        conn.execute("INSERT INTO short_videos (shop_id, title, description, video_path, created_at) VALUES (?, ?, ?, ?, ?)", (shop["id"], name.strip(), description.strip(), saved_media, dt.datetime.now().isoformat(timespec="minutes")))
                st.success("Produit ajouté et publié dans le défilé vidéo.")
                st.rerun()
    st.subheader("Produits et stock")
    with get_connection() as conn:
        own_products = conn.execute("SELECT * FROM products WHERE shop_id=? ORDER BY id DESC", (shop["id"],)).fetchall()
    for p in own_products:
        with st.expander(f"{p['name']} — stock : {p['stock']}"):
            with st.form(f"edit_{p['id']}"):
                price = st.number_input("Prix", min_value=0.0, value=float(p["price"]), step=0.5, key=f"price_{p['id']}")
                stock = st.number_input("Stock", min_value=0, value=int(p["stock"]), step=1, key=f"stock_{p['id']}")
                if st.form_submit_button("Mettre à jour"):
                    with get_connection() as conn:
                        conn.execute("UPDATE products SET price=?, stock=? WHERE id=? AND shop_id=?", (price, stock, p["id"], shop["id"]))
                    st.success("Produit mis à jour.")
                    st.rerun()
    with st.form("story"):
        content = st.text_area("Publier une story / un arrivage")
        if st.form_submit_button("Publier la story") and content.strip():
            with get_connection() as conn:
                conn.execute("INSERT INTO stories (shop_id, content, created_at) VALUES (?, ?, ?)", (shop["id"], content.strip(), dt.datetime.now().strftime("%Y-%m-%d %H:%M")))
                create_arrival_notifications(shop["id"], shop["name"], shop["address"] or "Lubumbashi", content.strip())
            st.success("Story publiée. Les abonnés concernés recevront une notification dans l’application.")
            st.rerun()


init_db()

# Écran de démarrage affiché une seule fois par session.
if "splash_seen" not in st.session_state:
    st.session_state.splash_seen = True
    st.markdown("<style>[data-testid='stSidebar']{display:none;} .block-container{max-width:100%; padding-top:4vh;}</style>", unsafe_allow_html=True)
    if BRAND_ICON:
        mime = "image/svg+xml" if BRAND_ICON.suffix.lower() == ".svg" else "image/png"
        encoded_icon = base64.b64encode(BRAND_ICON.read_bytes()).decode("ascii")
        icon_html = f"<img src='data:{mime};base64,{encoded_icon}' style='display:block; width:360px; max-width:80vw; margin:0 auto 24px auto;'>"
    else:
        icon_html = ""
    st.markdown(f"""
    <div style='width:100%; text-align:center;'>
      {icon_html}
      <h2>Bienvenue sur Lubumarket</h2>
      <p style='font-size:20px;'>Votre shopping commence ici.</p>
      <p style='font-size:17px;'>Découvrez les articles des boutiques de Lubumbashi.</p>
      <p style='font-size:17px;'>Trouvez, choisissez et commandez simplement.</p>
      <p style='font-size:15px;'>Chargement de votre espace shopping...</p>
    </div>
    """, unsafe_allow_html=True)
    time.sleep(6)
    st.rerun()

if BRAND_ICON:
    st.image(str(BRAND_ICON), width=150)
st.title("Lubumarket")
st.caption("Les produits des boutiques de Lubumbashi")
with st.sidebar:
    st.header("Navigation")
    menu = st.radio("Choisir un espace", ["Articles", "Arrivages", "Notifications", "S’abonner aux arrivages", "Commandes groupées", "Coursier pour essayage", "Créateurs & Stylistes", "Compte utilisateur", "Espace Commerçant"], index=0)
    st.caption("Les articles sont affichés directement. Le paiement n’est pas intégré : les demandes se confirment avec la boutique.")
if menu == "Articles":
    show_client()
elif menu == "Arrivages":
    show_arrivals()
elif menu == "Notifications":
    show_notifications()
elif menu == "S’abonner aux arrivages":
    show_notification_subscription()
elif menu == "Commandes groupées":
    show_group_orders()
elif menu == "Coursier pour essayage":
    show_courier_request()
elif menu == "Créateurs & Stylistes":
    show_creators()
elif menu == "Compte utilisateur":
    customer_auth()
else:
    show_merchant()