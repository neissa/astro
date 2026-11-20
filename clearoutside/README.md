# clearoutside-discord

Remplaçant du cron « ClearOutside Saucats » qui tournait sur neissa.org : chaque **vendredi et samedi à 16:00 (heure de Paris)**, il capture la prévision astro de [clearoutside.com](https://clearoutside.com/forecast/44.65/-0.60) et la poste sur un webhook Discord.

| | Ancien (neissa.org) | Nouveau |
|---|---|---|
| Capture | image maison `screenshot` (Debian jessie + PhantomJS 2.1.1, abandonné) | Chrome headless [`chromedp/headless-shell`](https://hub.docker.com/r/chromedp/headless-shell) (Chrome à jour, maintenu), piloté par le protocole DevTools |
| Cadrage | 1200×560 px à partir de y=450 (coordonnées fixes) | le **même cadrage** 1200×560 px, ancré sur le bandeau « Est. Sky Quality » au lieu d'une coordonnée fixe ; repli automatique sur le bloc complet du jour si le site change |
| Planification | crontab root du serveur | `crond` dans `python:3.13-alpine`, intégré au compose |
| Ressources | conteneur lancé à chaque capture (`docker run --rm`) | Chrome **arrêté en temps normal** : démarré pour la capture (≈ 5 s), puis arrêté ; seul le petit conteneur Python reste allumé |
| Disque / RAM | — | ≈ 615 Mo sur disque ; RAM < 1 Mo au repos, ≈ 175 Mo pendant les ~6 s de capture (voir [Ressources](#ressources)) |
| Envoi Discord | `curl` | Python (`urllib`) |

Aucun code à compiler ni dépendance à installer : deux images publiques et un script Python qui n'utilise que la bibliothèque standard.

## Installation

Sur n'importe quelle machine avec Docker (PC, NAS, Raspberry Pi 64 bits, VPS) :

```bash
git clone https://github.com/neissa/astro.git
cd astro/clearoutside
cp .env.example .env      # puis y coller l'URL du webhook Discord
docker compose up -d
```

Pour mettre à jour : `git pull`, puis `docker compose up -d --remove-orphans` (recrée ce qui a changé dans `compose.yaml`). Pour une mise à jour de Chrome : `docker compose pull chrome` avant.

Crée `.env` **avant** `docker compose up`, sinon Docker crée un dossier `.env` vide à sa place.

Juste après `up`, le planificateur arrête Chrome : `docker compose ps -a` le montre alors *exited*, c'est normal. À chaque capture, le script le démarre via l'API Docker, attend qu'il réponde, capture, puis l'arrête, même en cas d'erreur.

### Venant de la version Gotenberg

```bash
git pull
docker compose up -d --remove-orphans   # supprime le conteneur clearoutside-gotenberg
docker image rm gotenberg/gotenberg:9 curlimages/curl:8.23.0   # libère ≈ 2 Go
```

## Tester tout de suite

```bash
docker compose exec planificateur python /app/clearoutside.py
```

Pour juste générer l'image sans rien poster, laisse `DISCORD_WEBHOOK_URL` vide : l'image est écrite dans `/tmp/clearoutside.png` du conteneur (`docker compose cp planificateur:/tmp/clearoutside.png .`).

## Journal

```bash
docker compose logs -f planificateur
```

## Ressources

Mesuré avec `docker stats` et `docker system df` (amd64, Chrome 159) :

| | Disque | RAM au repos | RAM pendant une capture (~6 s) |
|---|---|---|---|
| `chrome` (`chromedp/headless-shell:stable`) | 543 Mo | 0 (arrêté) | ≈ 160 Mo (pic) |
| `planificateur` (`python:3.13-alpine`) | 71 Mo | < 1 Mo (`crond` seul) | ≈ 15 Mo (pic) |
| Conteneurs (couches modifiables) | ≈ 2 Mo | | |
| **Total** | **≈ 615 Mo** | **< 1 Mo** | **≈ 175 Mo** |

Le téléchargement initial est plus léger (images compressées : ≈ 160 Mo pour Chrome). Pour vérifier sur ta machine : `docker system df -v` et, pendant un test, `docker stats`.

Les journaux Docker (`docker compose logs`) grossissent de quelques lignes par capture ; rien d'autre n'est écrit sur le disque (l'image capturée reste dans `/tmp` du conteneur et est écrasée à chaque fois).

## Réglages

- **Lieu** : `CLEAROUTSIDE_URL` dans `.env` (latitude/longitude dans l'URL).
- **Horaire** : la ligne cron dans `entrypoint` de `compose.yaml` (`0 16 * * 5,6`).
- **Cadrage** : `CADRAGE` dans `.env`.
  - `origine` (défaut) : comme sur neissa.org, 1200×560 px, du bandeau « Est. Sky Quality » jusqu'à la ligne « Precipitation Type ». Le script injecte un cadre invisible de cette taille juste au-dessus du bandeau et découpe la capture dessus. Si le bandeau est introuvable (refonte du site), le script le signale dans le journal et se replie sur `jour`.
  - `jour` : le bloc complet du jour, avec toutes les lignes jusqu'à l'ozone (1142×863 px).
- **Fuseau** : `TZ` dans `compose.yaml`, au format POSIX parce que l'image Python Alpine n'embarque pas la base des fuseaux.

`.env` est relu à chaque capture : inutile de redémarrer quoi que ce soit après l'avoir modifié.

## Sécurité

`.env` contient l'URL du webhook, qui suffit pour poster dans le salon. Ne le committe pas, et régénère le webhook dans Discord si l'URL a fuité. Chrome n'expose aucun port sur l'hôte : seul le planificateur y accède (port DevTools 9222), par le réseau interne du compose.

Pour démarrer et arrêter Chrome, le planificateur a accès au socket Docker (`/var/run/docker.sock`), ce qui équivaut à un accès root à l'hôte. Il n'appelle que `start` et `stop` sur le conteneur `clearoutside-chrome`, et l'image `python` est officielle. Si tu préfères garder Chrome allumé en permanence (≈ 120 Mo de RAM), retire cette ligne de `compose.yaml` et remplace `restart: "no"` par `restart: unless-stopped` sur `chrome` : le script détecte l'absence du socket et ne touche plus au conteneur.
