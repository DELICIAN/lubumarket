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
        """)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(shops)").fetchall()]
        if "owner_id" not in columns:
            conn.execute("ALTER TABLE shops ADD COLUMN owner_id INTEGER")
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


def load_products():
    with get_connection() as conn:
        return conn.execute("""SELECT p.*, s.name shop_name, s.address, s.whatsapp, s.is_open
        FROM products p JOIN shops s ON p.shop_id=s.id ORDER BY p.id DESC""").fetchall()


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
    all_products = load_products()
    products = [p for p in all_products if neighborhood_matches(neighborhood, p["address"], p["shop_name"]) and query in normalize_text(f"{p['name']} {p['description']}") and (not only_open or p["is_open"])]
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
                        st.image(p["image_url"], use_container_width=True)
                    except Exception:
                        st.caption("Image indisponible")
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
        uploaded = st.file_uploader("Ajouter l’image depuis votre téléphone", type=["jpg", "jpeg", "png", "webp"])
        if st.form_submit_button("Ajouter le produit"):
            if not name.strip():
                st.error("Le nom du produit est obligatoire.")
            else:
                with get_connection() as conn:
                    conn.execute("INSERT INTO products (shop_id, name, description, price, image_url, stock) VALUES (?, ?, ?, ?, ?, ?)", (shop["id"], name.strip(), description.strip(), price, save_uploaded_image(uploaded), stock))
                st.success("Produit ajouté.")
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
            st.success("Story publiée.")
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
    menu = st.radio("Choisir un espace", ["Articles", "Espace Commerçant"], index=0)
    st.caption("Les articles sont affichés directement. L’espace commerçant est réservé aux boutiques.")
if menu == "Articles":
    show_client()
else:
    show_merchant()
