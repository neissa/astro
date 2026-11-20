"""Capture la prévision ClearOutside et la poste sur un webhook Discord.

Remplace docker-screenshot (PhantomJS) de neissa.org : la capture est faite par Chrome headless
(image chromedp/headless-shell), piloté par le protocole DevTools (CDP). Bibliothèque standard seule.
Chrome reste arrêté entre deux captures : ce script le démarre, s'en sert, puis l'arrête.

    python clearoutside.py                  capture + envoi
    python clearoutside.py --arreter-chrome arrête juste Chrome (utilisé au démarrage du planificateur)
"""
import base64
import http.client
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid


def charger_env(chemin):
    """Lit un fichier CLE=valeur (comme `. .env` en sh) ; ses valeurs priment sur l'environnement."""
    try:
        with open(chemin, encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                cle, valeur = ligne.split("=", 1)
                cle = cle.strip().removeprefix("export ").strip()
                valeur = valeur.strip()
                if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "'\"":
                    valeur = valeur[1:-1]
                os.environ[cle] = valeur
    except FileNotFoundError:
        pass


charger_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

CLEAROUTSIDE_URL = os.environ.get("CLEAROUTSIDE_URL") or "https://clearoutside.com/forecast/44.65/-0.60"
CHROME_HOST = os.environ.get("CHROME_HOST") or "127.0.0.1:9222"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_USERNAME = os.environ.get("DISCORD_USERNAME") or "ClearOutside Saucats"
SORTIE = os.environ.get("SORTIE") or "/tmp/clearoutside.png"
# origine : le cadrage de neissa.org, 1200×560 px à partir du bandeau « Est. Sky Quality »
# jour    : le bloc complet du jour (toutes les lignes, du nuage à l'ozone)
CADRAGE = os.environ.get("CADRAGE") or "origine"
# Conteneur Chrome à démarrer/arrêter via l'API Docker (socket monté par compose.yaml).
# Vide, ou socket absent : Chrome est supposé déjà lancé et n'est pas touché.
CHROME_CONTAINER = os.environ.get("CHROME_CONTAINER", "")
DOCKER_SOCK = os.environ.get("DOCKER_SOCK") or "/var/run/docker.sock"

# Pour retrouver le cadrage d'origine, on injecte un cadre invisible de 1200×560 px, centré sur la page
# et ancré 3 px au-dessus du bandeau « Sky Quality », puis on découpe la capture sur sa boîte.
CADRE_JS = """(() => {
  const bandeau = document.querySelector(".content [class*=btn-bortle]");
  if (bandeau && !document.getElementById("cadre-origine")) {
    const cadre = document.createElement("div");
    cadre.id = "cadre-origine";
    cadre.style.cssText = "position:absolute;left:calc(50% - 600px);width:1200px;height:560px;margin-top:-3px;pointer-events:none";
    bandeau.parentNode.insertBefore(cadre, bandeau);
  }
  return !!bandeau;
})()"""

# Boîte de l'élément en coordonnées de la page. x est borné à 0 : Chrome ignore tout le découpage
# (et renvoie le haut de la page) si la zone déborde à gauche.
BOITE_JS = """(selecteur => {
  const e = document.querySelector(selecteur);
  if (!e) return null;
  const r = e.getBoundingClientRect();
  return {x: Math.max(0, r.left + scrollX), y: r.top + scrollY, width: r.width, height: r.height};
})(%s)"""


def journal(message):
    print(time.strftime("%F %T"), message, flush=True)


# --- Pilotage du conteneur Chrome par l'API Docker ---------------------------------------------

class ConnexionUnix(http.client.HTTPConnection):
    def __init__(self, chemin):
        super().__init__("localhost", timeout=30)
        self.chemin = chemin

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.chemin)


def pilotage_actif():
    return bool(CHROME_CONTAINER) and os.path.exists(DOCKER_SOCK)


def docker_api(action):
    """action = start | stop ; renvoie le code HTTP (204 = fait, 304 = déjà dans cet état)."""
    try:
        c = ConnexionUnix(DOCKER_SOCK)
        c.request("POST", f"/containers/{CHROME_CONTAINER}/{action}")
        r = c.getresponse()
        r.read()
        return r.status
    except OSError as e:
        return f"{type(e).__name__} {e}"


def chrome_pret():
    """Renvoie (ip, port, chemin WebSocket du navigateur) si Chrome répond, sinon None."""
    hote, port = CHROME_HOST.rsplit(":", 1)
    try:
        ip = socket.gethostbyname(hote)  # Chrome refuse un en-tête Host qui n'est ni une IP ni localhost
        with urllib.request.urlopen(f"http://{ip}:{port}/json/version", timeout=2) as r:
            url_ws = json.load(r)["webSocketDebuggerUrl"]
    except (OSError, ValueError, KeyError):
        return None
    return ip, int(port), "/" + url_ws.split("/", 3)[3]


def demarrer_chrome():
    if pilotage_actif():
        code = docker_api("start")
        if code not in (204, 304):
            raise RuntimeError(f"Démarrage de Chrome impossible (API Docker : {code})")
    for i in range(60):
        pret = chrome_pret()
        if pret:
            if pilotage_actif():
                journal(f"Chrome démarré (prêt en {i} s)")
            return pret
        time.sleep(1)
    raise RuntimeError(f"Chrome ne répond pas sur {CHROME_HOST} après 60 s")


def arreter_chrome():
    if not pilotage_actif():
        return
    code = docker_api("stop")
    if code == 204:
        journal("Chrome arrêté")
    elif code != 304:
        journal(f"Arrêt de Chrome impossible (API Docker : {code})")


# --- Protocole DevTools (CDP) sur un WebSocket minimal -----------------------------------------

class WebSocket:
    """Client WebSocket réduit à ce dont CDP a besoin : messages texte, pas d'extensions."""

    def __init__(self, ip, port, chemin):
        self.s = socket.create_connection((ip, port), timeout=60)
        cle = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {chemin} HTTP/1.1\r\nHost: {ip}:{port}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {cle}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        entete = b""
        while b"\r\n\r\n" not in entete:
            entete += self._lire(1)
        if b" 101 " not in entete.split(b"\r\n", 1)[0]:
            raise RuntimeError("Connexion DevTools refusée : " + entete.decode(errors="replace").strip())

    def _lire(self, n):
        donnees = b""
        while len(donnees) < n:
            morceau = self.s.recv(n - len(donnees))
            if not morceau:
                raise ConnectionError("Connexion DevTools fermée")
            donnees += morceau
        return donnees

    def envoyer(self, texte):
        donnees = texte.encode()
        n = len(donnees)
        if n < 126:
            tete = struct.pack(">BB", 0x81, 0x80 | n)
        elif n < 65536:
            tete = struct.pack(">BBH", 0x81, 0x80 | 126, n)
        else:
            tete = struct.pack(">BBQ", 0x81, 0x80 | 127, n)
        masque = os.urandom(4)
        self.s.sendall(tete + masque + bytes(b ^ masque[i % 4] for i, b in enumerate(donnees)))

    def recevoir(self):
        message = b""
        while True:
            b0, b1 = self._lire(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._lire(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._lire(8))[0]
            charge = self._lire(n)
            code = b0 & 0x0F
            if code == 8:
                raise ConnectionError("Connexion DevTools fermée par Chrome")
            if code in (0, 1, 2):  # suite, texte, binaire ; ping/pong ignorés
                message += charge
                if b0 & 0x80:
                    return json.loads(message)

    def fermer(self):
        self.s.close()


class Onglet:
    """Un onglet Chrome, piloté par une session CDP « à plat » sur la connexion du navigateur."""

    def __init__(self, ws):
        self.ws, self.n, self.evenements, self.session = ws, 0, set(), None
        self.cible = self.appel("Target.createTarget", url="about:blank")["targetId"]
        self.session = self.appel("Target.attachToTarget", targetId=self.cible, flatten=True)["sessionId"]

    def appel(self, methode, **params):
        self.n += 1
        message = {"id": self.n, "method": methode, "params": params}
        if self.session:
            message["sessionId"] = self.session
        self.ws.envoyer(json.dumps(message))
        while True:
            m = self.ws.recevoir()
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{methode} : {m['error'].get('message')}")
                return m.get("result", {})
            self.evenements.add(m.get("method"))

    def attendre(self, evenement, delai=60):
        fin = time.monotonic() + delai
        while evenement not in self.evenements:
            if time.monotonic() > fin:
                raise TimeoutError(f"{evenement} non reçu après {delai} s")
            self.evenements.add(self.ws.recevoir().get("method"))

    def evaluer(self, expression):
        r = self.appel("Runtime.evaluate", expression=expression, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError("Erreur JavaScript : " + r["exceptionDetails"].get("text", ""))
        return r["result"].get("value")

    def fermer(self):
        self.session = None
        self.appel("Target.closeTarget", targetId=self.cible)


def capturer(ip, port, chemin_ws):
    """Charge la page, choisit la zone selon CADRAGE et écrit le PNG dans SORTIE."""
    ws = WebSocket(ip, port, chemin_ws)
    try:
        onglet = Onglet(ws)
        try:
            # Fenêtre de 1200 px de large, assez haute pour contenir la zone (le bandeau de cookies,
            # en bas, reste de toute façon hors du cadre), sans barre de défilement qui la rétrécirait.
            onglet.appel("Emulation.setDeviceMetricsOverride", width=1200, height=2000, deviceScaleFactor=1, mobile=False)
            onglet.appel("Emulation.setScrollbarsHidden", hidden=True)
            onglet.appel("Network.setCookie", name="cookieconsent_status", value="dismiss",
                         domain="clearoutside.com", path="/")
            onglet.appel("Page.enable")
            nav = onglet.appel("Page.navigate", url=CLEAROUTSIDE_URL)
            if nav.get("errorText"):
                raise RuntimeError(f"Chargement de {CLEAROUTSIDE_URL} impossible : {nav['errorText']}")
            onglet.attendre("Page.loadEventFired")
            time.sleep(3)  # laisse les scripts de la page finir de remplir le tableau

            selecteur = "#day_0"
            if CADRAGE != "jour":
                if onglet.evaluer(CADRE_JS):
                    selecteur = "#cadre-origine"
                else:
                    journal("Cadrage d'origine impossible : bandeau « Sky Quality » introuvable "
                            "(mise en page du site modifiée ?). Repli sur le bloc du jour.")
            boite = onglet.evaluer(BOITE_JS % json.dumps(selecteur))
            if not boite:
                raise RuntimeError(f"Zone {selecteur} introuvable dans la page")
            png = onglet.appel("Page.captureScreenshot", format="png", clip={**boite, "scale": 1})
        finally:
            onglet.fermer()
    finally:
        ws.fermer()
    with open(SORTIE, "wb") as f:
        f.write(base64.b64decode(png["data"]))
    journal(f"capture {selecteur} ({boite['width']:.0f}×{boite['height']:.0f} px) écrite dans {SORTIE}")


# --- Envoi Discord -------------------------------------------------------------------------------

def envoyer_discord():
    frontiere = uuid.uuid4().hex
    with open(SORTIE, "rb") as f:
        image = f.read()
    payload = json.dumps({"username": DISCORD_USERNAME, "content": ""})
    corps = (f"--{frontiere}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
             f"Content-Type: application/json\r\n\r\n{payload}\r\n"
             f"--{frontiere}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"clearoutside.png\"\r\n"
             f"Content-Type: image/png\r\n\r\n").encode() + image + f"\r\n--{frontiere}--\r\n".encode()
    sep = "&" if "?" in DISCORD_WEBHOOK_URL else "?"
    requete = urllib.request.Request(DISCORD_WEBHOOK_URL + sep + "wait=true", data=corps, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={frontiere}",
        "User-Agent": "clearoutside-discord (python urllib)",  # Cloudflare bloque l'User-Agent par défaut de urllib
    })
    try:
        with urllib.request.urlopen(requete, timeout=60) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Échec de l'envoi Discord (HTTP {e.code}) : {e.read()[:500].decode(errors='replace')}")
    journal("envoyé sur Discord")


def main():
    if sys.argv[1:] == ["--arreter-chrome"]:
        arreter_chrome()
        return 0
    try:
        # Quoi qu'il arrive (succès, erreur, interruption), Chrome est arrêté avant l'envoi Discord.
        try:
            ip, port, chemin_ws = demarrer_chrome()
            journal(f"capture de {CLEAROUTSIDE_URL} (cadrage {CADRAGE})")
            capturer(ip, port, chemin_ws)
        finally:
            arreter_chrome()
        if not DISCORD_WEBHOOK_URL:
            journal(f"DISCORD_WEBHOOK_URL vide : image enregistrée dans {SORTIE}, rien n'est envoyé.")
            return 0
        envoyer_discord()
        return 0
    except (OSError, RuntimeError) as e:
        journal(f"ERREUR : {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
