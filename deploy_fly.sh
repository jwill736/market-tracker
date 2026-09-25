#!/usr/bin/env bash
# Put Plumbline on an always-on server (Fly.io, about $4-5 a month) with one command:
#   ./deploy_fly.sh
# It installs nothing on your machine except Fly's CLI (if missing), asks for three secrets,
# creates the app and a 1 GB disk for your data, and deploys. Run it again to update.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v fly >/dev/null 2>&1 && ! command -v flyctl >/dev/null 2>&1; then
  echo "Installing Fly's command-line tool..."
  curl -fsSL https://fly.io/install.sh | sh
  export PATH="$HOME/.fly/bin:$PATH"
fi
FLY=$(command -v fly || command -v flyctl)

"$FLY" auth whoami >/dev/null 2>&1 || "$FLY" auth login

APP=$(grep -E '^app *=' fly.toml | sed -E 's/app *= *"([^"]+)".*/\1/')
if [ "$APP" = "plumbline-change-me" ]; then
  APP="plumbline-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
  sed -i.bak -E "s/^app *= *\"plumbline-change-me\"/app = \"$APP\"/" fly.toml && rm -f fly.toml.bak
  echo "App name: $APP"
fi

if ! "$FLY" apps list 2>/dev/null | grep "^$APP[[:space:]]" >/dev/null; then
  "$FLY" apps create "$APP"
fi
if ! "$FLY" volumes list -a "$APP" 2>/dev/null | grep plumbline_data >/dev/null; then
  "$FLY" volumes create plumbline_data --size 1 --region "$(grep -E '^primary_region' fly.toml | sed -E 's/.*"(.+)".*/\1/')" -a "$APP" --yes
fi

if ! "$FLY" secrets list -a "$APP" 2>/dev/null | grep APP_PASSWORD >/dev/null; then
  read -r -s -p "Choose a password for the app (you'll type it to sign in): " PW; echo
  read -r -p "Your email for the SEC contact (e.g. you@gmail.com): " EMAIL
  read -r -p "ntfy topic for phone alerts (Enter to skip): " TOPIC
  ARGS=(APP_PASSWORD="$PW" SEC_USER_AGENT="plumbline $EMAIL")
  [ -n "$TOPIC" ] && ARGS+=(NTFY_TOPIC="$TOPIC")
  "$FLY" secrets set -a "$APP" "${ARGS[@]}" --stage
fi

"$FLY" deploy -a "$APP" --ha=false
echo
echo "Plumbline is live at https://$APP.fly.dev"
echo "To move your data: in your local app, Portfolio → Backup → Download; then on the server, Portfolio → Backup → Restore."
