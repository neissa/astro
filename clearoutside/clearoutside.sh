#!/bin/sh
# Capture la prévision ClearOutside et la poste sur un webhook Discord.
# Remplace docker-screenshot (PhantomJS) de neissa.org : la capture est faite par Gotenberg (Chromium).
# Gotenberg reste arrêté entre deux captures : ce script le démarre, s'en sert, puis l'arrête.
#   sh clearoutside.sh                      capture + envoi
#   sh clearoutside.sh --arreter-gotenberg  arrête juste Gotenberg (utilisé au démarrage du planificateur)
set -eu

ICI=$(cd "$(dirname "$0")" && pwd)
[ -f "$ICI/.env" ] && . "$ICI/.env"

CLEAROUTSIDE_URL="${CLEAROUTSIDE_URL:-https://clearoutside.com/forecast/44.65/-0.60}"
GOTENBERG_URL="${GOTENBERG_URL:-http://127.0.0.1:3000}"
DISCORD_USERNAME="${DISCORD_USERNAME:-ClearOutside Saucats}"
SORTIE="${SORTIE:-/tmp/clearoutside.png}"
# origine : le cadrage de neissa.org, 1200×560 px à partir du bandeau « Est. Sky Quality »
# jour    : le bloc complet du jour (toutes les lignes, du nuage à l'ozone)
CADRAGE="${CADRAGE:-origine}"
# Conteneur Gotenberg à démarrer/arrêter via l'API Docker (socket monté par compose.yaml).
# Vide, ou socket absent : Gotenberg est supposé déjà lancé et n'est pas touché.
GOTENBERG_CONTAINER="${GOTENBERG_CONTAINER:-}"
DOCKER_SOCK="${DOCKER_SOCK:-/var/run/docker.sock}"

pilotage_actif() { [ -n "$GOTENBERG_CONTAINER" ] && [ -S "$DOCKER_SOCK" ]; }

docker_api() { # $1 = start | stop ; affiche le code HTTP (204 = fait, 304 = déjà dans cet état)
  curl -s --unix-socket "$DOCKER_SOCK" -o /dev/null -w '%{http_code}' -X POST \
    "http://localhost/containers/$GOTENBERG_CONTAINER/$1" || true
}

demarrer_gotenberg() {
  pilotage_actif || return 0
  code=$(docker_api start)
  case "$code" in 204|304) ;; *) echo "Démarrage de Gotenberg impossible (API Docker : HTTP $code)"; return 1 ;; esac
  i=0
  until curl -sf --max-time 2 -o /dev/null "$GOTENBERG_URL/health"; do
    i=$((i + 1))
    [ "$i" -lt 60 ] || { echo "Gotenberg ne répond pas après 60 s"; return 1; }
    sleep 1
  done
  echo "Gotenberg démarré (prêt en ${i} s)"
}

arreter_gotenberg() {
  pilotage_actif || return 0
  code=$(docker_api stop)
  case "$code" in
    204) echo "Gotenberg arrêté" ;;
    304) ;;
    *) echo "Arrêt de Gotenberg impossible (API Docker : HTTP $code)" ;;
  esac
}

if [ "${1:-}" = "--arreter-gotenberg" ]; then
  arreter_gotenberg
  exit 0
fi

# Gotenberg ne sait découper que la boîte d'un élément. Pour retrouver le cadrage d'origine, on injecte
# un cadre invisible de 1200×560 px, centré sur la page et ancré 3 px au-dessus du bandeau « Sky Quality » :
# il suit la mise en page quand Gotenberg passe la fenêtre à 1200 px de large, juste avant la capture.
CADRE_JS='(() => {
  const bandeau = document.querySelector(".content [class*=btn-bortle]");
  if (bandeau && !document.getElementById("cadre-origine")) {
    const cadre = document.createElement("div");
    cadre.id = "cadre-origine";
    cadre.style.cssText = "position:absolute;left:calc(50% - 600px);width:1200px;height:560px;margin-top:-3px;pointer-events:none";
    bandeau.parentNode.insertBefore(cadre, bandeau);
  }
  return true;
})()'

capture() { # $1 = sélecteur CSS de la zone à capturer
  # cookies : masque le bandeau de consentement ; height=2000 le garde de toute façon hors du cadre.
  curl -sS --fail-with-body --max-time 120 -o "$SORTIE" \
    --form-string "url=$CLEAROUTSIDE_URL" \
    --form-string "selector=$1" \
    --form-string "waitForExpression=$CADRE_JS" \
    --form-string 'width=1200' \
    --form-string 'height=2000' \
    --form-string 'format=png' \
    --form-string 'waitDelay=3s' \
    --form-string 'cookies=[{"name":"cookieconsent_status","value":"dismiss","domain":"clearoutside.com"}]' \
    "$GOTENBERG_URL/forms/chromium/screenshot/url"
}

# Quoi qu'il arrive (succès, erreur, interruption), Gotenberg est arrêté en sortie.
trap arreter_gotenberg EXIT
demarrer_gotenberg

echo "$(date '+%F %T') capture de $CLEAROUTSIDE_URL (cadrage $CADRAGE)"
if [ "$CADRAGE" = "jour" ]; then
  capture '#day_0'
elif ! capture '#cadre-origine'; then
  echo "Cadrage d'origine impossible (mise en page du site modifiée ?) : $(head -c 300 "$SORTIE")"
  echo "Repli sur le bloc du jour."
  capture '#day_0'
fi

# La capture est faite : inutile de garder Gotenberg pendant l'envoi Discord.
arreter_gotenberg
trap - EXIT

if [ -z "${DISCORD_WEBHOOK_URL:-}" ]; then
  echo "DISCORD_WEBHOOK_URL vide : image enregistrée dans $SORTIE, rien n'est envoyé."
  exit 0
fi

curl -sS --fail-with-body --max-time 60 -o /tmp/discord-reponse.json \
  -F "file=@$SORTIE;type=image/png;filename=clearoutside.png" \
  --form-string "payload_json={\"username\":\"$DISCORD_USERNAME\",\"content\":\"\"}" \
  "$DISCORD_WEBHOOK_URL?wait=true" \
  || { echo "Échec de l'envoi Discord :"; cat /tmp/discord-reponse.json; echo; exit 1; }
echo "$(date '+%F %T') envoyé sur Discord"
