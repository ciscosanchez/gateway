#!/bin/sh
# Render alertmanager.yml.tpl -> /tmp/alertmanager.yml with env substitution,
# then exec alertmanager. Only %%ALERTMANAGER_SLACK_WEBHOOK%% and
# %%ALERTMANAGER_PAGERDUTY_KEY%% are substituted — Go-template braces
# ({{ ... }}) used by alertmanager itself are preserved, and the %%...%%
# markers avoid sed's regex treatment of ${} (where $ anchors end-of-line).
#
# If an env var is unset, we substitute a placeholder value that still
# parses as a valid URL / string so alertmanager doesn't fail config
# validation on startup. Receivers will just no-op (send will fail, alerts
# still fire through other receivers).
set -eu

TPL=/etc/alertmanager/alertmanager.yml.tpl
OUT=/tmp/alertmanager.yml

SLACK="${ALERTMANAGER_SLACK_WEBHOOK:-}"
if [ -z "$SLACK" ]; then
  SLACK="https://hooks.slack.com/services/UNSET"
fi

PD="${ALERTMANAGER_PAGERDUTY_KEY:-}"
if [ -z "$PD" ]; then
  PD="UNSET-PAGERDUTY-ROUTING-KEY"
fi

sed \
  -e "s#%%ALERTMANAGER_SLACK_WEBHOOK%%#${SLACK}#g" \
  -e "s#%%ALERTMANAGER_PAGERDUTY_KEY%%#${PD}#g" \
  "$TPL" > "$OUT"

exec /bin/alertmanager "$@"
