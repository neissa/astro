# clearoutside-discord

Remplaçant du cron « ClearOutside Saucats » qui tournait sur neissa.org : chaque **vendredi et samedi à 16:00 (heure de Paris)**, il capture la prévision astro de [clearoutside.com](https://clearoutside.com/forecast/44.65/-0.60) et la poste sur un webhook Discord.

| | Ancien (neissa.org) | Nouveau |
|---|---|---|
| Capture | image maison `screenshot` (Debian jessie + PhantomJS 2.1.1, abandonné) | [Gotenberg](https://gotenberg.dev) `gotenberg/gotenberg:9` (Chromium à jour, maintenu) |
| Cadrage | 1200×560 px à partir de y=450 (coordonnées fixes) | le **même cadrage** 1200×560 px, ancré sur le bandeau « Est. Sky Quality » au lieu d'une coordonnée fixe ; repli automatique sur le bloc complet du jour si le site change |
| Planification | crontab root du serveur | `crond` dans `curlimages/curl`, intégré au compose |
| Ressources | conteneur lancé à chaque capture (`docker run --rm`) | Gotenberg **arrêté en temps normal** : démarré pour la capture (≈ 10 s), puis arrêté ; seul le petit conteneur `curl` reste allumé |
| Envoi Discord | `curl` | `curl` (identique) |

Aucun code à compiler : deux images publiques et un script `sh`.

## Installation

Sur n'importe quelle machine avec Docker (PC, NAS, Raspberry Pi 64 bits, VPS) :

```bash
git clone https://github.com/neissa/astro.git
cd astro/clearoutside
cp .env.example .env      # puis y coller l'URL du webhook Discord
docker compose up -d
```

Pour mettre à jour : `git pull`, puis `docker compose restart planificateur`.

Crée `.env` **avant** `docker compose up`, sinon Docker crée un dossier `.env` vide à sa place.

Juste après `up`, le planificateur arrête Gotenberg : `docker compose ps -a` le montre alors *exited*, c'est normal. À chaque capture, le script le démarre via l'API Docker, attend qu'il réponde, capture, puis l'arrête, même en cas d'erreur.

## Tester tout de suite

```bash
docker compose exec planificateur sh /app/clearoutside.sh
```

Pour juste générer l'image sans rien poster, laisse `DISCORD_WEBHOOK_URL` vide : l'image est écrite dans `/tmp/clearoutside.png` du conteneur (`docker compose cp planificateur:/tmp/clearoutside.png .`).

## Journal

```bash
docker compose logs -f planificateur
```

## Réglages

- **Lieu** : `CLEAROUTSIDE_URL` dans `.env` (latitude/longitude dans l'URL).
- **Horaire** : la ligne cron dans `entrypoint` de `compose.yaml` (`0 16 * * 5,6`).
- **Cadrage** : `CADRAGE` dans `.env`.
  - `origine` (défaut) : comme sur neissa.org, 1200×560 px, du bandeau « Est. Sky Quality » jusqu'à la ligne « Precipitation Type ». Gotenberg ne sait découper que la boîte d'un élément : le script injecte un cadre invisible de cette taille juste au-dessus du bandeau et découpe dessus. Si le bandeau est introuvable (refonte du site), le script le signale dans le journal et se replie sur `jour`.
  - `jour` : le bloc complet du jour, avec toutes les lignes jusqu'à l'ozone (1142×863 px).
- **Fuseau** : `TZ` dans `compose.yaml`, au format POSIX parce que l'image `curl` n'embarque pas la base des fuseaux.

## Sécurité

`.env` contient l'URL du webhook, qui suffit pour poster dans le salon. Ne le committe pas, et régénère le webhook dans Discord si l'URL a fuité. Gotenberg n'expose aucun port sur l'hôte : seul le planificateur y accède, par le réseau interne du compose.

Pour démarrer et arrêter Gotenberg, le planificateur a accès au socket Docker (`/var/run/docker.sock`), ce qui équivaut à un accès root à l'hôte. Il n'appelle que `start` et `stop` sur le conteneur `clearoutside-gotenberg`, et l'image `curlimages/curl` est officielle. Si tu préfères garder Gotenberg allumé en permanence, retire cette ligne de `compose.yaml` et remets `restart: unless-stopped` sur `gotenberg` : le script détecte l'absence du socket et ne touche plus au conteneur.
