#!/bin/sh
# Capture la prévision ClearOutside et la poste sur un webhook Discord.
# Remplace docker-screenshot (PhantomJS) de neissa.org : la capture est faite par Gotenberg (Chromium).
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

echo "$(date '+%F %T') capture de $CLEAROUTSIDE_URL (cadrage $CADRAGE)"
if [ "$CADRAGE" = "jour" ]; then
  capture '#day_0'
elif ! capture '#cadre-origine'; then
  echo "Cadrage d'origine impossible (mise en page du site modifiée ?) : $(head -c 300 "$SORTIE")"
  echo "Repli sur le bloc du jour."
  capture '#day_0'
fi

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
